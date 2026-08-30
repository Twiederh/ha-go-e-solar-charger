"""Glue between Home Assistant state and the pure logic in pv_logic.py."""
import logging
from datetime import timedelta
from typing import Optional

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util

from .const import (
    CONF_GOE_API_KEY,
    CONF_GOE_HOST,
    CONF_PV_BATTERY_ENTITY,
    CONF_PV_DEFAULT_THRESHOLD,
    CONF_PV_EXPORT_OVERRIDE_THRESHOLD,
    CONF_PV_GRID_ENTITY,
    CONF_PV_SOC_ENTITY,
    CONF_PV_SOLAR_ENTITY,
    DEFAULT_PV_EXPORT_OVERRIDE_THRESHOLD,
    DEFAULT_PV_THRESHOLD,
    PV_PUSH_KEEPALIVE_INTERVAL_SECONDS,
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
        self.export_override_w: float = config.get(
            CONF_PV_EXPORT_OVERRIDE_THRESHOLD, DEFAULT_PV_EXPORT_OVERRIDE_THRESHOLD
        )
        self.enabled: bool = True
        self.status_text: str = "Initialisiere ..."

        # Exposed as sensor attributes (see sensor.py) so the actual
        # go-e payload and the raw readings behind it can be checked
        # directly in the UI, without having to trust the status text or
        # go digging through the logs.
        self.last_read_values: dict = {}
        self.last_pushed_values: Optional[dict] = None
        self.last_pushed_at = None

        self._unsub_track = None
        self._unsub_interval = None
        # Set by __init__.py right after construction, if the cheap-grid-
        # charging feature is configured - lets it pause this feature
        # entirely (not even the zeroed safety values) on days it takes
        # over instead.
        self._suppressed_by = None

    @property
    def signal(self) -> str:
        return f"{SIGNAL_PV_STATUS_UPDATE}_{self.entry.entry_id}"

    def set_suppressor(self, controller) -> None:
        self._suppressed_by = controller

    async def async_setup(self) -> None:
        entities = [self._solar_entity, self._grid_entity, self._battery_entity, self._soc_entity]
        self._unsub_track = async_track_state_change_event(self.hass, entities, self._handle_event)
        # go-e treats a missing pPv/pGrid/pAkku update as "PV source is
        # gone" after a few seconds and pauses charging - so re-send on a
        # timer too, not just when a source sensor happens to change.
        self._unsub_interval = async_track_time_interval(
            self.hass,
            self._handle_tick,
            timedelta(seconds=PV_PUSH_KEEPALIVE_INTERVAL_SECONDS),
        )
        await self.async_evaluate()

    def async_unload(self) -> None:
        if self._unsub_track:
            self._unsub_track()
            self._unsub_track = None
        if self._unsub_interval:
            self._unsub_interval()
            self._unsub_interval = None

    @callback
    def _handle_event(self, event: Event) -> None:
        self.hass.async_create_task(self.async_evaluate())

    @callback
    def _handle_tick(self, now) -> None:
        self.hass.async_create_task(self.async_evaluate())

    def _read_float(self, entity_id: str):
        state = self.hass.states.get(entity_id)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None
        try:
            return float(state.state)
        except (TypeError, ValueError):
            return None

    async def async_evaluate(self) -> None:
        # Recorded regardless of what happens below, so the raw inputs
        # behind the decision can always be checked in the UI - including
        # while suppressed or disabled.
        self.last_read_values = {
            "solar_w": self._read_float(self._solar_entity),
            "grid_w": self._read_float(self._grid_entity),
            "battery_w": self._read_float(self._battery_entity),
            "powerwall_soc": self._read_float(self._soc_entity),
        }

        if self._suppressed_by is not None and self._suppressed_by.suppress_pv:
            self.status_text = "Pausiert (Guenstigstrom-Tag aktiv)"
            self.last_pushed_values = None
            async_dispatcher_send(self.hass, self.signal)
            return

        result = evaluate(
            PvPushInput(
                enabled=self.enabled,
                powerwall_soc=self.last_read_values["powerwall_soc"],
                threshold=self.threshold,
                solar_w=self.last_read_values["solar_w"],
                grid_w=self.last_read_values["grid_w"],
                battery_w=self.last_read_values["battery_w"],
                export_override_w=self.export_override_w,
            )
        )

        # What we actually computed and attempted to send - kept even if
        # the send itself below fails, since the point is to verify the
        # *values*, not just whether the HTTP call succeeded (the status
        # text already says so separately).
        self.last_pushed_values = result.values

        if result.values is not None:
            # Always send - go-e needs a fresh value at least every ~5s
            # (see PV_PUSH_KEEPALIVE_INTERVAL_SECONDS) or it pauses
            # charging, so there is no "too often" here, only "too rare".
            try:
                await self._goe.push_pv_values(result.values)
                self.last_pushed_at = dt_util.utcnow()
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("Konnte PV-Werte nicht an go-e senden: %s", exc)
                result.status_text += " - Senden an go-e fehlgeschlagen, versuche es weiter"

        self.status_text = result.status_text
        async_dispatcher_send(self.hass, self.signal)

    async def async_set_threshold(self, value: float) -> None:
        self.threshold = value
        await self.async_evaluate()

    async def async_set_export_override(self, value: float) -> None:
        self.export_override_w = value
        await self.async_evaluate()

    async def async_set_enabled(self, value: bool) -> None:
        self.enabled = value
        await self.async_evaluate()

    async def async_manual_push(self) -> None:
        """Immediate push regardless of threshold - used by the "Jetzt
        senden" button, mainly to test the go-e connection."""
        await self.async_evaluate()
