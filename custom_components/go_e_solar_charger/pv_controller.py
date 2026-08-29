"""Glue between Home Assistant state and the pure logic in pv_logic.py."""
import logging
import time

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_state_change_event

from .const import (
    CONF_GOE_API_KEY,
    CONF_GOE_HOST,
    CONF_PV_BATTERY_ENTITY,
    CONF_PV_DEFAULT_THRESHOLD,
    CONF_PV_GRID_ENTITY,
    CONF_PV_SOC_ENTITY,
    CONF_PV_SOLAR_ENTITY,
    DEFAULT_PV_THRESHOLD,
    PV_PUSH_MIN_INTERVAL_SECONDS,
    SIGNAL_PV_STATUS_UPDATE,
)
from .goe_client import GoEClient
from .pv_logic import PvPushInput, evaluate

_LOGGER = logging.getLogger(__name__)


class PvSurplusController:
    """One instance per config entry. Feeds pPv/pGrid/pAkku to go-e once the
    Powerwall's own SoC is above a configurable threshold."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        config = {**entry.data, **entry.options}
        self._solar_entity = config[CONF_PV_SOLAR_ENTITY]
        self._grid_entity = config[CONF_PV_GRID_ENTITY]
        self._battery_entity = config[CONF_PV_BATTERY_ENTITY]
        self._soc_entity = config[CONF_PV_SOC_ENTITY]
        self._goe = GoEClient(
            async_get_clientsession(hass),
            config[CONF_GOE_HOST],
            config.get(CONF_GOE_API_KEY, ""),
        )

        # Set from restored entity state right after platform setup, before
        # async_setup() runs its first evaluation - see __init__.py.
        self.threshold: float = config.get(CONF_PV_DEFAULT_THRESHOLD, DEFAULT_PV_THRESHOLD)
        self.enabled: bool = True
        self.status_text: str = "Initialisiere ..."

        self._unsub_track = None
        self._last_push_monotonic: float = 0.0

    @property
    def signal(self) -> str:
        return f"{SIGNAL_PV_STATUS_UPDATE}_{self.entry.entry_id}"

    async def async_setup(self) -> None:
        entities = [self._solar_entity, self._grid_entity, self._battery_entity, self._soc_entity]
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

    async def async_evaluate(self, *, force: bool = False) -> None:
        result = evaluate(
            PvPushInput(
                enabled=self.enabled,
                powerwall_soc=self._read_float(self._soc_entity),
                threshold=self.threshold,
                solar_w=self._read_float(self._solar_entity),
                grid_w=self._read_float(self._grid_entity),
                battery_w=self._read_float(self._battery_entity),
            )
        )

        if result.values is not None:
            now = time.monotonic()
            if force or now - self._last_push_monotonic >= PV_PUSH_MIN_INTERVAL_SECONDS:
                try:
                    await self._goe.push_pv_values(result.values)
                    self._last_push_monotonic = now
                except Exception as exc:  # noqa: BLE001
                    _LOGGER.warning("Konnte PV-Werte nicht an go-e senden: %s", exc)
                    result.status_text += " - Senden an go-e fehlgeschlagen, versuche es weiter"

        self.status_text = result.status_text
        async_dispatcher_send(self.hass, self.signal)

    async def async_set_threshold(self, value: float) -> None:
        # Explicit user action (not a sensor tick) - bypass the push
        # throttle so e.g. raising the threshold above the current SoC
        # takes effect (zeros sent) immediately instead of waiting out
        # the last real push's cooldown.
        self.threshold = value
        await self.async_evaluate(force=True)

    async def async_set_enabled(self, value: bool) -> None:
        # Same reasoning as async_set_threshold: a deliberate toggle
        # should not be swallowed by the sensor-tick throttle.
        self.enabled = value
        await self.async_evaluate(force=True)

    async def async_manual_push(self) -> None:
        """Immediate push regardless of threshold/throttle - used by the
        "Jetzt senden" button, mainly to test the go-e connection."""
        await self.async_evaluate(force=True)
