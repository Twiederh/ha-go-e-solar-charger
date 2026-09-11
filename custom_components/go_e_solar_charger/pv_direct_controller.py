"""Glue between Home Assistant state and the pure logic in
pv_direct_logic.py.

Shares its threshold/export_override_w/enabled and its four Powerwall
source sensors with pv_controller.py (PvSurplusController) - both are
alternative "outputs" of the same SoC-gate, switched via that controller's
`control_mode` (see select.py's PvControlModeSelect). Only one of the two
is ever actually driving the charger at a time; async_evaluate() below
goes inert (releasing go-e back to Neutral, same as when disabled) unless
`control_mode` is PV_CONTROL_MODE_DIRECT.

Must manage go-e's "frc" (start/stop) itself, unlike PvSurplusController -
see pv_direct_logic.py's module docstring. That means it can fight
ZoeChargeLimitController's SoC-based stop unless coordinated: this reads
`zoe_controller.force_off_active` *before* deciding to charge (so it never
even tries to start against an active limit-stop), and additionally calls
`on_frc_changed` (== zoe_controller.async_evaluate) after every frc-
affecting action, exactly like cheap_controller.py already does - so a
stop that happens to race with this feature still gets the last word.
"""
import logging
import time
from dataclasses import replace
from datetime import timedelta
from typing import Optional

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval

from .const import (
    CAR_STATE_CHARGING,
    CONF_GOE_API_KEY,
    CONF_GOE_HOST,
    CONF_PV_BATTERY_ENTITY,
    CONF_PV_DIRECT_MAX_AMP,
    CONF_PV_GRID_ENTITY,
    CONF_PV_SOC_ENTITY,
    CONF_PV_SOLAR_ENTITY,
    DEFAULT_PV_DIRECT_MAX_AMP,
    PSM_AUTO,
    PSM_FORCE_1_PHASE,
    PSM_FORCE_3_PHASE,
    PV_CONTROL_MODE_DIRECT,
    PV_DIRECT_PHASE_SWITCH_HYSTERESIS_W,
    PV_DIRECT_REASSERT_INTERVAL_SECONDS,
    PV_DIRECT_STOP_REASSERT_INTERVAL_SECONDS,
    SIGNAL_PV_DIRECT_STATUS_UPDATE,
)
from .goe_client import GoEClient
from .pv_direct_logic import (
    ACTION_RELEASE,
    ACTION_START,
    ACTION_STOP,
    ACTION_UPDATE,
    PvDirectInput,
    evaluate,
)

_LOGGER = logging.getLogger(__name__)


class PvDirectController:
    """One instance per config entry. Alternative to PvSurplusController:
    computes the charging current/phase count itself and sets it directly
    on the go-e, instead of feeding it pPv/pGrid/pAkku."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        pv_controller,
        zoe_controller=None,
        on_frc_changed=None,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self._pv = pv_controller
        self._zoe_controller = zoe_controller
        self._on_frc_changed = on_frc_changed
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
        self.max_amp: float = config.get(CONF_PV_DIRECT_MAX_AMP, DEFAULT_PV_DIRECT_MAX_AMP)
        self.status_text: str = "Initialisiere ..."

        # Our own belief about what's currently applied - not read back
        # from go-e (see pv_direct_logic.py's module docstring for why).
        self._charging_active: bool = False
        self._active_amp: Optional[float] = None
        self._active_phase: Optional[int] = None
        self._last_applied_at: Optional[float] = None

        # Exposed as sensor attributes (see sensor.py), same rationale as
        # PvSurplusController's last_read_values/last_pushed_values.
        self.last_read_values: dict = {}
        self.last_computed_values: dict = {}
        # Raw go-e "car" (carState) value behind the car_actually_charging
        # sanity check above - None while not charging (never read) or on
        # a failed read, otherwise const.py's CAR_STATE_* integer.
        self.last_car_state: Optional[int] = None
        # Edge-triggered guard for the frc re-assert below: True once we've
        # already re-sent frc=On for the *current* confirmed-not-charging
        # stretch, so a stuck car (e.g. genuinely unplugged) gets one
        # immediate retry rather than a fresh "frc=On" on every single
        # evaluation - reset back to False as soon as go-e stops confirming
        # "not charging" (goes back to True, or to unknown/None).
        self._frc_reasserted_for_stall: bool = False
        # Raw go-e "nrg[11]" reading behind actual_car_draw_w below - None
        # while not charging (never read) or on a failed/unparsable read,
        # otherwise the live measured total power in Watts.
        self.last_total_power_w: Optional[float] = None

        self._unsub_track = None
        self._unsub_interval = None
        # Set by __init__.py right after construction, if the cheap-grid-
        # charging feature is configured - lets it pause this feature
        # entirely on days it takes over instead (same as pv_controller.py).
        self._suppressed_by = None

    @property
    def signal(self) -> str:
        return f"{SIGNAL_PV_DIRECT_STATUS_UPDATE}_{self.entry.entry_id}"

    def set_suppressor(self, controller) -> None:
        self._suppressed_by = controller

    async def async_setup(self) -> None:
        entities = [self._solar_entity, self._grid_entity, self._battery_entity, self._soc_entity]
        self._unsub_track = async_track_state_change_event(self.hass, entities, self._handle_event)
        # Amp/psm are persistent charger settings (unlike ids, they don't
        # expire on their own) - this timer exists purely for the periodic
        # re-assert described in the module docstring, not to keep go-e
        # from pausing.
        self._unsub_interval = async_track_time_interval(
            self.hass,
            self._handle_tick,
            timedelta(seconds=PV_DIRECT_REASSERT_INTERVAL_SECONDS),
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

    async def _read_car_actually_charging(self) -> Optional[bool]:
        """None on any read failure - treated by pv_direct_logic.py as
        "unknown, keep trusting the assumption" rather than "confirmed not
        charging", so a transient go-e/network hiccup can't interrupt an
        otherwise-fine charge."""
        try:
            car_state = await self._goe.get_car_state()
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("Konnte go-e-Ladezustand nicht lesen: %s", exc)
            self.last_car_state = None
            return None
        self.last_car_state = car_state
        if car_state is None:
            return None
        return car_state == CAR_STATE_CHARGING

    async def _read_actual_car_draw(self) -> Optional[float]:
        """None on any read failure - treated by pv_direct_logic.py as
        "unknown, fall back to the amp/phase guess" rather than "drawing
        zero", so a transient go-e/network hiccup can't wrongly stop or
        throttle an otherwise-fine charge."""
        try:
            power_w = await self._goe.get_total_power_w()
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("Konnte go-e-Ladeleistung nicht lesen: %s", exc)
            self.last_total_power_w = None
            return None
        self.last_total_power_w = power_w
        return power_w

    async def async_evaluate(self) -> None:
        self.last_read_values = {
            "solar_w": self._read_float(self._solar_entity),
            "grid_w": self._read_float(self._grid_entity),
            "battery_w": self._read_float(self._battery_entity),
            "powerwall_soc": self._read_float(self._soc_entity),
        }

        if self._suppressed_by is not None and self._suppressed_by.suppress_pv:
            await self._apply(
                ACTION_RELEASE if self._charging_active else None,
                "Pausiert (Guenstigstrom-Tag aktiv)",
                None,
                None,
            )
            return

        if self._pv.control_mode != PV_CONTROL_MODE_DIRECT:
            await self._apply(
                ACTION_RELEASE if self._charging_active else None,
                "Inaktiv (Werte senden aktiv)",
                None,
                None,
            )
            return

        zoe_force_off = (
            self._zoe_controller.force_off_active if self._zoe_controller is not None else False
        )

        # Fetched while we believe we're driving a charge for
        # pv_direct_logic.py's assumed-draw sanity check (see its module
        # docstring): without it, a car that stopped drawing current for
        # any reason we didn't request would have its last-requested
        # amp/phase phantom-added into the surplus forever. Also fetched
        # (see below) while we believe charging is already stopped, to
        # catch the symmetric problem: a stop that silently didn't take
        # effect on go-e's side, which would otherwise go unnoticed and
        # keep pulling grid power indefinitely - reported in practice.
        car_actually_charging = None
        if self._charging_active or (self._pv.enabled and not zoe_force_off):
            car_actually_charging = await self._read_car_actually_charging()

        # Only meaningful while we believe we're driving a charge - see
        # pv_direct_logic.py's module docstring ("third gap") and
        # goe_client.py's get_total_power_w().
        actual_car_draw_w = None
        if self._charging_active:
            actual_car_draw_w = await self._read_actual_car_draw()

        result = evaluate(
            PvDirectInput(
                enabled=self._pv.enabled,
                powerwall_soc=self.last_read_values["powerwall_soc"],
                threshold=self._pv.threshold,
                solar_w=self.last_read_values["solar_w"],
                grid_w=self.last_read_values["grid_w"],
                battery_w=self.last_read_values["battery_w"],
                export_override_w=self._pv.export_override_w,
                max_amp=self.max_amp,
                charging_active=self._charging_active,
                active_amp=self._active_amp,
                active_phase=self._active_phase,
                car_actually_charging=car_actually_charging,
                actual_car_draw_w=actual_car_draw_w,
                zoe_force_off_active=zoe_force_off,
                phase_switch_hysteresis_w=PV_DIRECT_PHASE_SWITCH_HYSTERESIS_W,
            )
        )

        reassert_frc = False
        if (
            result.action is None
            and result.charging_active
            and self._charging_active
            and self._last_applied_at is not None
            and (time.monotonic() - self._last_applied_at) >= PV_DIRECT_REASSERT_INTERVAL_SECONDS
        ):
            # Re-apply the same amp/phase/frc defensively, in case a manual
            # override at the charger (or the car being unplugged and
            # reconnected) silently changed go-e's actual state without
            # our knowledge - see the module docstring / const.py's
            # PV_DIRECT_REASSERT_INTERVAL_SECONDS. Also re-sends frc=On,
            # not just amp/psm (see below for why that matters).
            result = replace(
                result, action=ACTION_UPDATE, target_amp=self._active_amp, target_phase=self._active_phase
            )
            reassert_frc = True

        # Reported in practice: go-e's "frc" can apparently revert on its
        # own (a plug/unplug cycle, an internal error, ...) without our
        # tracked amp/phase changing at all - and ACTION_UPDATE (unlike
        # ACTION_START) never re-sends frc=On below, so a surplus kept
        # being computed and shown in the status text while the car never
        # actually resumed charging. Once go-e's own carState confirms
        # that, force one immediate frc=On re-send alongside whatever
        # amp/phase evaluate() has just computed - edge-triggered (only
        # once per confirmed-not-charging stretch) so a car that's
        # genuinely not going to charge (unplugged, real error) doesn't
        # get spammed with frc=On on every single evaluation; the periodic
        # re-assert above still retries roughly every
        # PV_DIRECT_REASSERT_INTERVAL_SECONDS after that.
        attempt_stall_reassert = False
        if car_actually_charging is False and not self._frc_reasserted_for_stall:
            if result.action is None and result.charging_active:
                result = replace(result, action=ACTION_UPDATE)
            reassert_frc = True
            attempt_stall_reassert = True
        elif car_actually_charging is not False:
            self._frc_reasserted_for_stall = False

        # Symmetric to the above, reported in practice: once this feature
        # believes it already stopped the car (surplus dropped below the
        # minimum, sent frc=Off), it never checked again whether go-e
        # actually stopped drawing current - a stop that silently didn't
        # take effect would then pull grid power indefinitely with nothing
        # ever re-sending "stop". Retried much more often than the
        # frc=On stall guard above (PV_DIRECT_STOP_REASSERT_INTERVAL_SECONDS,
        # not edge-triggered) since this actively costs money for as long
        # as it goes unnoticed, and a resent frc=Off is a plain, low-risk
        # command (unlike psm - see the bugfix above). Only while this
        # feature is actually the one that should be deciding frc at all
        # (enabled, not deferring to the Auto charge limit's own stop).
        if (
            self._pv.enabled
            and not zoe_force_off
            and not result.charging_active
            and car_actually_charging is True
            and (
                self._last_applied_at is None
                or (time.monotonic() - self._last_applied_at)
                >= PV_DIRECT_STOP_REASSERT_INTERVAL_SECONDS
            )
        ):
            result = replace(result, action=ACTION_STOP)

        self.last_computed_values = {
            "available_power_w": result.available_power_w,
            "target_amp": result.target_amp,
            "target_phase": result.target_phase,
            "car_actually_charging": car_actually_charging,
            "goe_car_state": self.last_car_state,
            "actual_car_draw_w": actual_car_draw_w,
        }
        applied_ok = await self._apply(
            result.action, result.status_text, result.target_amp, result.target_phase, reassert_frc
        )
        if attempt_stall_reassert and applied_ok:
            # Only latch the "already retried" guard once the frc=On
            # re-send actually went through - on a failed attempt (go-e
            # unreachable, rejected the command, ...) the next evaluation
            # should try again rather than silently giving up on the first
            # network hiccup.
            self._frc_reasserted_for_stall = True

    async def _apply(
        self,
        action: Optional[str],
        status_text: str,
        target_amp,
        target_phase,
        reassert_frc: bool = False,
    ) -> bool:
        try:
            if action == ACTION_RELEASE:
                await self._goe.release()
                await self._goe.set_phase_mode(PSM_AUTO)
            elif action == ACTION_STOP:
                await self._goe.stop_charging()
            elif action in (ACTION_START, ACTION_UPDATE):
                # Reported in practice: charging via go-e's own logic starts
                # immediately, but via this feature it never actually gets
                # going despite go-e accepting every individual command -
                # while every amp-only ACTION_UPDATE (i.e. most evaluations,
                # since export power fluctuates constantly) used to resend
                # "psm" too, even completely unchanged. If go-e treats any
                # psm write as a manual override that re-arms its own
                # app-side mode-confirmation gate, that would both explain
                # the repeatedly reappearing "tap to continue" prompt AND
                # mean charging could never survive past the next
                # evaluation. Now only sent on an actual start, or when the
                # phase count is actually changing - never on a bare amp
                # adjustment. Also kinder to the phase-switch relay itself,
                # which is physical hardware with a limited switching life.
                if action == ACTION_START or target_phase != self._active_phase:
                    await self._goe.set_phase_mode(
                        PSM_FORCE_3_PHASE if target_phase == 3 else PSM_FORCE_1_PHASE
                    )
                await self._goe.set_amp(int(target_amp))
                if action == ACTION_START or reassert_frc:
                    await self._goe.force_charging_on()
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Direkte Ladesteuerung: Befehl an go-e fehlgeschlagen: %s", exc)
            self.status_text = f"{status_text} - Befehl an go-e fehlgeschlagen, versuche es weiter"
            async_dispatcher_send(self.hass, self.signal)
            # Deliberately leave _charging_active/_active_amp/_active_phase
            # untouched so the next evaluation retries applying `action`,
            # rather than silently believing the change already happened.
            return False

        if action in (ACTION_START, ACTION_UPDATE):
            self._charging_active = True
            self._active_amp = target_amp
            self._active_phase = target_phase
        elif action in (ACTION_RELEASE, ACTION_STOP):
            self._charging_active = False
            self._active_amp = None
            self._active_phase = None

        if action is not None:
            self._last_applied_at = time.monotonic()
            # reassert_frc also re-sends frc=On via an ACTION_UPDATE (see
            # above) - that's just as frc-affecting as a fresh ACTION_START,
            # so the Auto charge limit feature must get the same chance to
            # immediately re-assert its own stop on top of it.
            if self._on_frc_changed and (
                reassert_frc or action in (ACTION_RELEASE, ACTION_STOP, ACTION_START)
            ):
                await self._on_frc_changed()

        self.status_text = status_text
        async_dispatcher_send(self.hass, self.signal)
        return True

    async def async_set_max_amp(self, value: float) -> None:
        self.max_amp = value
        await self.async_evaluate()

    async def async_manual_test(self) -> None:
        """Re-applies the current decision immediately - useful to verify
        the go-e connection without waiting for the next sensor change or
        timer tick."""
        await self.async_evaluate()
