"""Glue between Home Assistant state and the pure logic in tesla_logic.py.

Turning switch.turn_on/turn_off once when the decision changes isn't
enough on its own: the Tesla's own (solar-aware) charging logic can decide
on its own to resume charging while it's plugged in - e.g. its own
scheduled/smart charging - regardless of what our switch last told it.
Observed in practice: the SoC-gate decision stayed "gestoppt" for hours
straight while the Powerwall charged from 59 % to 84 %, yet the car kept
(re)starting its own charging in that time.

Periodically re-asserting the decision (see REASSERT_INTERVAL_SECONDS
below) does correct that - but it does so by actually re-issuing a stop
command to a Tesla that may have resumed charging in the meantime, which
means real, repeated start/stop cycling of the actual charging session
every time that happens, not just a corrected status text. That trade-off
turned out to be worse than the original problem, so this whole daytime
PV-surplus gating is switched off for now (DAYTIME_GATING_ENABLED = False)
at the user's request, while the mechanism stays in place, ready to be
re-enabled (and hopefully improved - e.g. by asking the Tesla whether it's
actually charging rather than blindly re-sending the switch command) later.
The Guenstigstrom night-charging window (cheap_controller.py, via
async_force_charge below) is a separate, unaffected code path.
"""
import logging
import time
from typing import Optional

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_state_change_event

from .const import (
    CONF_PV_GRID_ENTITY,
    CONF_PV_SOC_ENTITY,
    CONF_TESLA_CAR_NAME,
    CONF_TESLA_GRID_RELEASE_THRESHOLD,
    CONF_TESLA_SWITCH_ENTITY,
    DEFAULT_TESLA_CAR_NAME,
    DEFAULT_TESLA_GRID_RELEASE_THRESHOLD,
    SIGNAL_TESLA_STATUS_UPDATE,
)
from .tesla_logic import TeslaChargeInput, evaluate

_LOGGER = logging.getLogger(__name__)

NOT_CONFIGURED_TEXT = (
    'Nicht konfiguriert - bitte unter "Konfigurieren" den Tesla-Lade-Schalter angeben.'
)

# How often the current stop/go decision is re-applied even while it
# hasn't changed, to override the Tesla's own charging logic if it tries
# to resume on its own. Comfortably under the ~5-minute update cadence the
# Powerwall SoC/grid sensors are typically seen at, so a real restart gets
# caught within one evaluation cycle - but not so tight that it would spam
# the Tesla API if those sensors happened to update much faster.
REASSERT_INTERVAL_SECONDS = 240

# Temporarily takes the whole daytime PV/SoC-based gating out of service -
# see the module docstring above for why. While False, async_evaluate()
# never touches the switch on its own initiative; the Tesla's own charging
# logic runs completely unmanaged during the day. async_force_charge()
# (the Guenstigstrom night-window's exclusive control channel) is a
# separate code path and keeps working regardless of this flag.
DAYTIME_GATING_ENABLED = False
STATUS_TEXT_DAYTIME_DISABLED = (
    "Tagsueber vorerst deaktiviert - nur das Guenstigstrom-Nachtladen "
    "steuert dieses Fahrzeug"
)


class TeslaChargingController:
    """One instance per config entry. Starts/stops the Tesla's own
    charging via a plain switch, gated by the same Powerwall SoC/grid
    sensors already configured for the PV-surplus-push feature - reusing
    its *live* threshold (`pv_controller.threshold`), so adjusting that
    number there also affects this feature."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, pv_controller) -> None:
        self.hass = hass
        self.entry = entry
        self._pv_controller = pv_controller
        config = {**entry.data, **entry.options}
        self._switch_entity = config.get(CONF_TESLA_SWITCH_ENTITY)
        self._soc_entity = config.get(CONF_PV_SOC_ENTITY)
        self._grid_entity = config.get(CONF_PV_GRID_ENTITY)
        self._configured = bool(self._switch_entity and self._soc_entity)
        # Free-text name for this car - used in its own entity names and
        # wherever the cheap-grid-charging feature refers to it by name.
        self.car_name: str = config.get(CONF_TESLA_CAR_NAME) or DEFAULT_TESLA_CAR_NAME

        # Set from restored entity state right after platform setup, before
        # async_setup() runs its first evaluation - see __init__.py.
        self.grid_release_threshold: float = config.get(
            CONF_TESLA_GRID_RELEASE_THRESHOLD, DEFAULT_TESLA_GRID_RELEASE_THRESHOLD
        )
        self.enabled: bool = True
        self.status_text: str = "Initialisiere ..."
        # None until the first evaluation actually decides something -
        # ensures the switch is driven into a defined state at startup
        # regardless of whatever it happened to be left at.
        self._last_applied: Optional[bool] = None
        # monotonic() timestamp of the last time we actually called the
        # switch service - used to throttle the periodic re-assertion
        # described above (None = never applied yet, always due).
        self._last_applied_at: Optional[float] = None
        self._suppressed_by = None

        self._unsub_track = None

    @property
    def signal(self) -> str:
        return f"{SIGNAL_TESLA_STATUS_UPDATE}_{self.entry.entry_id}"

    @property
    def configured(self) -> bool:
        return self._configured

    def set_suppressor(self, controller) -> None:
        """The cheap-grid-charging controller, if any - while its
        `suppress_tesla` is true, this controller's own PV/export-based
        gating goes inert and the Tesla switch is driven exclusively via
        async_force_charge() instead."""
        self._suppressed_by = controller

    async def async_setup(self) -> None:
        if not self._configured:
            self.status_text = NOT_CONFIGURED_TEXT
            async_dispatcher_send(self.hass, self.signal)
            return

        if DAYTIME_GATING_ENABLED:
            entities = [self._soc_entity]
            if self._grid_entity:
                entities.append(self._grid_entity)
            self._unsub_track = async_track_state_change_event(
                self.hass, entities, self._handle_event
            )
        await self.async_evaluate()

    def async_unload(self) -> None:
        if self._unsub_track:
            self._unsub_track()
            self._unsub_track = None

    @callback
    def _handle_event(self, event: Event) -> None:
        self.hass.async_create_task(self.async_evaluate())

    def _read_float(self, entity_id):
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None
        try:
            return float(state.state)
        except (TypeError, ValueError):
            return None

    async def _set_tesla_switch(self, on: bool) -> None:
        try:
            await self.hass.services.async_call(
                "switch",
                "turn_on" if on else "turn_off",
                {"entity_id": self._switch_entity},
                blocking=True,
            )
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning(
                "Konnte %s nicht %s: %s",
                self._switch_entity,
                "einschalten" if on else "ausschalten",
                exc,
            )

    async def async_evaluate(self) -> None:
        if not self._configured:
            self.status_text = NOT_CONFIGURED_TEXT
            async_dispatcher_send(self.hass, self.signal)
            return

        if self._suppressed_by is not None and self._suppressed_by.suppress_tesla:
            self.status_text = "Pausiert (Guenstigstrom-Tag aktiv)"
            async_dispatcher_send(self.hass, self.signal)
            return

        if not DAYTIME_GATING_ENABLED:
            self.status_text = STATUS_TEXT_DAYTIME_DISABLED
            async_dispatcher_send(self.hass, self.signal)
            return

        result = evaluate(
            TeslaChargeInput(
                enabled=self.enabled,
                powerwall_soc=self._read_float(self._soc_entity),
                soc_threshold=self._pv_controller.threshold,
                grid_w=self._read_float(self._grid_entity),
                grid_release_threshold_w=self.grid_release_threshold,
            )
        )

        if result.should_charge is not None:
            changed = result.should_charge != self._last_applied
            due = (
                self._last_applied_at is None
                or (time.monotonic() - self._last_applied_at) >= REASSERT_INTERVAL_SECONDS
            )
            if changed or due:
                await self._set_tesla_switch(result.should_charge)
                self._last_applied = result.should_charge
                self._last_applied_at = time.monotonic()

        self.status_text = result.status_text
        async_dispatcher_send(self.hass, self.signal)

    async def async_set_grid_release_threshold(self, value: float) -> None:
        self.grid_release_threshold = value
        await self.async_evaluate()

    async def async_set_enabled(self, value: bool) -> None:
        if not self._configured:
            self.enabled = value
            self.status_text = NOT_CONFIGURED_TEXT
            async_dispatcher_send(self.hass, self.signal)
            return

        if not value and self.enabled and self._last_applied is False:
            # Don't leave the Tesla stuck not-charging with nothing left
            # to ever turn it back on - hand control back to whatever its
            # own charging solution/limit would otherwise decide.
            await self._set_tesla_switch(True)
            self._last_applied = True
            self._last_applied_at = time.monotonic()

        self.enabled = value
        await self.async_evaluate()

    async def async_manual_test(self) -> None:
        """Re-applies the current decision immediately - useful to verify
        the switch connection without waiting for the next sensor
        change."""
        await self.async_evaluate()

    async def async_force_charge(self, on: bool) -> None:
        """Exclusive-control channel used by the cheap-grid-charging
        controller while it suppresses this controller's own PV/export-
        based gating (see set_suppressor above) - bypasses that
        suppression entirely, since this call *is* the suppressor acting."""
        if on != self._last_applied:
            await self._set_tesla_switch(on)
            self._last_applied = on
            self._last_applied_at = time.monotonic()
        self.status_text = (
            "Erzwungen (Guenstigstrom-Fenster aktiv)"
            if on
            else "Pausiert (Guenstigstrom-Fenster)"
        )
        async_dispatcher_send(self.hass, self.signal)
