"""Pure decision logic for the direct Zoe/go-e charging control feature
(amp + phase switching), kept free of any Home Assistant imports so it can
be unit tested in isolation.

Alternative to pv_logic.py's approach: instead of handing pPv/pGrid/pAkku
to go-e's own PV-surplus-charging algorithm, this feature computes the
target charging current (Amps) and phase count (1 or 3) itself and sets
them directly on the charger - see pv_direct_controller.py / goe_client.py
("amp"/"psm"). Offered as a switchable alternative (see select.py's
PvControlModeSelect) rather than a replacement, since go-e's own
documentation for phase-switching is inconsistent even in its own issue
tracker (goecharger/go-eCharger-API-v2 #58, #30) - this needs verifying
against the real charger, which is why every computed value here is also
exposed as a sensor attribute (see pv_direct_controller.py's
last_computed_values / sensor.py's PvDirectStatusSensor).

Same SoC-threshold/export-override gating as pv_logic.py (see there for
the rationale) - both features share the *same* threshold/
export_override_w, just as two alternative "outputs" of one gate.

Unlike pv_logic.py's approach, this feature must manage go-e's "frc"
(start/stop) itself: handing it a fixed amp value does not make it stop
on its own once the surplus disappears, the way go-e's own PV-surplus
algorithm does when fed pPv/pGrid/pAkku. To avoid needing to trust go-e's
own live measurement API (whose documentation is exactly the unreliable
part - see above), this treats its own last-commanded amp/phase as the
car's current draw while charging, rather than reading it back from go-e -
one fewer unverified API dependency, at the cost of not reacting to the
car temporarily drawing less than requested.

Also defers entirely to the Auto charge limit feature's SoC-based stop
(zoe_force_off_active below, mirroring ZoeChargeLimitController.
force_off_active) - this feature must never fight that or re-enable
charging out from under it.
"""
from dataclasses import dataclass
from typing import Optional

MIN_AMP = 6
VOLTAGE_V = 230

ACTION_RELEASE = "release"  # frc back to Neutral (feature disabled/inactive)
ACTION_STOP = "stop"  # frc to Off (no surplus, but feature still enabled)
ACTION_START = "start"  # set amp/psm, then frc to On
ACTION_UPDATE = "update"  # already charging via us - amp/psm changed (or a periodic re-assert)


@dataclass
class PvDirectInput:
    enabled: bool
    powerwall_soc: Optional[float]
    threshold: float
    solar_w: Optional[float]
    grid_w: Optional[float]  # negative = feeding into the grid
    battery_w: Optional[float]
    export_override_w: float
    max_amp: float
    # Whether *we* currently believe charging is active because of this
    # feature (mirrors ZoeChargeLimitController.force_off_active) - used
    # both to decide what stopping means here, and (while True) to treat
    # the last commanded amp/phase as the car's own current draw.
    charging_active: bool
    active_amp: Optional[float]
    active_phase: Optional[int]
    # True while the Auto charge limit feature has *independently*
    # force-stopped the car (SoC reached its limit) - takes priority over
    # everything below, so this feature never fights it or releases go-e
    # out from under it (see ZoeChargeLimitController.force_off_active).
    zoe_force_off_active: bool
    # Asymmetric hysteresis margin (Watts) around the 3-phase minimum -
    # avoids flapping right at that boundary.
    phase_switch_hysteresis_w: float


@dataclass
class PvDirectResult:
    status_text: str
    action: Optional[str]
    target_amp: Optional[float]
    target_phase: Optional[int]
    charging_active: bool
    # Diagnostics - filled in whenever computable, regardless of the
    # gating outcome, so they can be checked even while not charging.
    available_power_w: Optional[float] = None


def _clamp(value: float, min_value: float, max_value: float) -> float:
    return max(min_value, min(max_value, value))


def _stop_result(status_text: str, state: "PvDirectInput") -> PvDirectResult:
    action = ACTION_STOP if state.charging_active else None
    return PvDirectResult(status_text, action, None, None, False)


def _unchanged_result(status_text: str, state: "PvDirectInput") -> PvDirectResult:
    """Sensor data is missing/ambiguous - don't guess, just keep whatever
    was already happening until it comes back (mirrors zoe_logic.py's
    identical "soc not available" handling)."""
    return PvDirectResult(status_text, None, state.active_amp, state.active_phase, state.charging_active)


def evaluate(state: PvDirectInput) -> PvDirectResult:
    if not state.enabled:
        action = ACTION_RELEASE if state.charging_active else None
        return PvDirectResult("Deaktiviert", action, None, None, False)

    if state.zoe_force_off_active:
        # The SoC-based charge limit already stopped the car via its own
        # frc=Off - never touch frc here, just stop believing *we* are the
        # one charging it so a later evaluation starts from a clean slate.
        return PvDirectResult("Wartet - Ladelimit des Fahrzeugs aktiv", None, None, None, False)

    if state.powerwall_soc is None:
        return _unchanged_result("Akkustand der Powerwall nicht verfuegbar", state)

    below_threshold = state.powerwall_soc < state.threshold
    export_w = None if state.grid_w is None else -state.grid_w
    export_override = (
        below_threshold and export_w is not None and export_w > state.export_override_w
    )

    if below_threshold and not export_override:
        return _stop_result(
            f"Akkustand {state.powerwall_soc:.0f} % < {state.threshold:.0f} % "
            "- keine Ladung",
            state,
        )

    if state.solar_w is None or state.grid_w is None or state.battery_w is None:
        return _unchanged_result("Leistungswerte der Powerwall nicht verfuegbar", state)

    # What's currently spare: whatever the house is already exporting (or
    # importing, if negative) plus whatever the car itself is already
    # drawing because of a previous decision by this feature - the export
    # figure above already reflects that draw, so it needs adding back to
    # get the *total* available for the car, not just the leftover on top.
    assumed_car_draw_w = (
        state.active_amp * state.active_phase * VOLTAGE_V
        if state.charging_active and state.active_amp and state.active_phase
        else 0.0
    )
    available_power_w = export_w + assumed_car_draw_w

    min_amp = MIN_AMP
    max_amp = max(state.max_amp, min_amp)
    three_phase_min_w = min_amp * 3 * VOLTAGE_V
    one_phase_min_w = min_amp * VOLTAGE_V

    if available_power_w < one_phase_min_w:
        result = _stop_result(
            f"Ueberschuss {available_power_w:.0f} W < Minimum {one_phase_min_w:.0f} W "
            "- keine Ladung",
            state,
        )
        result.available_power_w = available_power_w
        return result

    # Asymmetric hysteresis: switching *up* to 3 phases needs clearing the
    # 3-phase minimum by a margin; once on 3 phases, switching back *down*
    # needs dropping below that minimum by the same margin - otherwise
    # hovering right at the boundary would flip phases on every evaluation.
    currently_three_phase = state.charging_active and state.active_phase == 3
    if currently_three_phase:
        use_three_phase = available_power_w >= three_phase_min_w - state.phase_switch_hysteresis_w
    else:
        use_three_phase = available_power_w >= three_phase_min_w + state.phase_switch_hysteresis_w

    if use_three_phase:
        target_phase = 3
        target_amp = round(_clamp(available_power_w / (3 * VOLTAGE_V), min_amp, max_amp))
    else:
        target_phase = 1
        target_amp = round(_clamp(available_power_w / VOLTAGE_V, min_amp, max_amp))

    changed = (
        not state.charging_active
        or target_amp != state.active_amp
        or target_phase != state.active_phase
    )
    action = (ACTION_UPDATE if state.charging_active else ACTION_START) if changed else None
    return PvDirectResult(
        f"Laedt direkt: {target_amp:.0f} A / {target_phase}-phasig "
        f"(Ueberschuss {available_power_w:.0f} W)",
        action,
        target_amp,
        target_phase,
        True,
        available_power_w,
    )
