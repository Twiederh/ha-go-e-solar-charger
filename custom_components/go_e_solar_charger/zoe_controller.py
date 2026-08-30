"""Glue between Home Assistant state and the pure logic in zoe_logic.py."""
import logging

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_state_change_event

from .const import (
    CONF_GOE_API_KEY,
    CONF_GOE_HOST,
    CONF_ZOE_CAR_CONNECTED_ENTITY,
    CONF_ZOE_CAR_CONNECTED_ON_STATE,
    CONF_ZOE_CAR_NAME,
    CONF_ZOE_CHARGING_ENTITY,
    CONF_ZOE_CHARGING_ON_STATE,
    CONF_ZOE_DEFAULT_LIMIT,
    CONF_ZOE_SOC_ENTITY,
    DEFAULT_ZOE_CAR_CONNECTED_ON_STATE,
    DEFAULT_ZOE_CAR_NAME,
    DEFAULT_ZOE_CHARGING_ON_STATE,
    DEFAULT_ZOE_LIMIT,
    SIGNAL_ZOE_STATUS_UPDATE,
)
from .goe_client import GoEClient
from .zoe_logic import ACTION_RESET, ACTION_STOP, ChargeLimitInput, evaluate

_LOGGER = logging.getLogger(__name__)


class ZoeChargeLimitController:
    """One instance per config entry. Owns the current limit/enabled state
    (kept in sync with the number/switch entities), listens for changes on
    the source sensors, and talks to go-e when the decision logic says to.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        config = {**entry.data, **entry.options}
        self._soc_entity = config[CONF_ZOE_SOC_ENTITY]
        self._charging_entity = config[CONF_ZOE_CHARGING_ENTITY]
        self._charging_on_state = config.get(
            CONF_ZOE_CHARGING_ON_STATE, DEFAULT_ZOE_CHARGING_ON_STATE
        )
        self._car_connected_entity = config.get(CONF_ZOE_CAR_CONNECTED_ENTITY)
        self._car_connected_on_state = config.get(
            CONF_ZOE_CAR_CONNECTED_ON_STATE, DEFAULT_ZOE_CAR_CONNECTED_ON_STATE
        )
        # Free-text name for this car - used in its own entity names and
        # wherever the cheap-grid-charging feature refers to it by name.
        self.car_name: str = config.get(CONF_ZOE_CAR_NAME) or DEFAULT_ZOE_CAR_NAME
        self._goe = GoEClient(
            async_get_clientsession(hass),
            config[CONF_GOE_HOST],
            config.get(CONF_GOE_API_KEY, ""),
        )

        # Set from restored entity state right after platform setup, before
        # async_setup() runs its first evaluation - see __init__.py.
        self.limit: float = config.get(CONF_ZOE_DEFAULT_LIMIT, DEFAULT_ZOE_LIMIT)
        self.enabled: bool = True
        self.force_off_active: bool = False
        self.status_text: str = "Initialisiere ..."

        self._unsub_track = None

    @property
    def signal(self) -> str:
        return f"{SIGNAL_ZOE_STATUS_UPDATE}_{self.entry.entry_id}"

    @property
    def car_label(self) -> str:
        """Display label used for this car's own entity names and by the
        cheap-grid-charging feature when referring to it - e.g. "Zoe
        Ladelimit" if the car was named "Zoe", or "Auto Ladelimit" with the
        default name."""
        return f"{self.car_name} Ladelimit"

    async def async_setup(self) -> None:
        entities = [self._soc_entity, self._charging_entity]
        if self._car_connected_entity:
            entities.append(self._car_connected_entity)
        self._unsub_track = async_track_state_change_event(self.hass, entities, self._handle_event)
        await self.async_evaluate()

    def async_unload(self) -> None:
        if self._unsub_track:
            self._unsub_track()
            self._unsub_track = None

    @callback
    def _handle_event(self, event: Event) -> None:
        self.hass.async_create_task(self.async_evaluate())

    def _read_float(self, entity_id: str):
        state = self.hass.states.get(entity_id)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None
        try:
            return float(state.state)
        except (TypeError, ValueError):
            return None

    def _read_bool(self, entity_id, on_state: str) -> bool:
        if not entity_id:
            return False
        state = self.hass.states.get(entity_id)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return False
        return state.state.strip().lower() == on_state.strip().lower()

    async def async_evaluate(self) -> None:
        soc = self._read_float(self._soc_entity)
        charging = self._read_bool(self._charging_entity, self._charging_on_state)
        car_connected = (
            self._read_bool(self._car_connected_entity, self._car_connected_on_state)
            if self._car_connected_entity
            else None
        )

        result = evaluate(
            ChargeLimitInput(
                enabled=self.enabled,
                charging=charging,
                car_connected=car_connected,
                soc=soc,
                limit=self.limit,
                force_off_active=self.force_off_active,
            )
        )

        if result.action == ACTION_STOP:
            try:
                await self._goe.stop_charging()
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("Konnte go-e nicht stoppen: %s", exc)
                result.status_text += " - Befehl an go-e fehlgeschlagen, versuche es weiter"
                # leave force_off_active True so the next evaluation retries
        elif result.action == ACTION_RESET:
            try:
                await self._goe.release()
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("Konnte go-e nicht freigeben: %s", exc)
                result.status_text += " - Freigabe an go-e fehlgeschlagen, versuche es weiter"
                # better to assume it's still stopped than to silently think
                # charging resumed when the command never arrived
                result.force_off_active = True

        self.force_off_active = result.force_off_active
        self.status_text = result.status_text
        async_dispatcher_send(self.hass, self.signal)

    async def async_set_limit(self, value: float) -> None:
        self.limit = value
        await self.async_evaluate()

    async def async_set_enabled(self, value: bool) -> None:
        self.enabled = value
        await self.async_evaluate()

    async def async_manual_stop(self) -> None:
        """Immediate stop regardless of SoC - used by the "Jetzt stoppen"
        button, mainly to test the go-e connection without waiting for the
        real limit to be reached."""
        try:
            await self._goe.stop_charging()
            self.force_off_active = True
            self.status_text = "Manuell gestoppt"
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Manueller Stopp fehlgeschlagen: %s", exc)
            self.status_text = f"Manueller Stopp fehlgeschlagen: {exc}"
        async_dispatcher_send(self.hass, self.signal)
