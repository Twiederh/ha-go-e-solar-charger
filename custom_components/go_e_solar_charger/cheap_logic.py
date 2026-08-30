"""Pure decision logic for the cheap-grid-charging feature, kept free of
any Home Assistant imports so it can be unit tested in isolation.

Two independent daily rhythms drive this feature:

1. Once a day (see CHEAP_FORECAST_EVAL_HOUR/MINUTE in const.py, evaluated
   by the controller), the current solar forecast for "tomorrow" is
   compared against a threshold and latched as "is_low_solar_day" for the
   day that is about to start at the next midnight.
2. A price sensor swings between a cheap and an expensive rate; crossing
   below the price threshold marks the start of the cheap window, crossing
   back above marks its end. In this project's setup the cheap window
   always starts at local midnight, which conveniently doubles as the
   moment the previous day's latched decision becomes "today's" decision.

On a low-solar day, both the go-e-connected car ("Auto Ladelimit") and,
if configured, a second car with its own charging solution ("Tesla") are
force-charged during the cheap window. The Powerwall can charge itself
from the grid too (up to its own hardware limit) - while it does, only
one of the two cars may draw power at a time, decided by a configurable
priority; once it stops, both resume.

This module only computes the relevant booleans and the resulting
actions/status text - all the timing (when to sample the forecast, when
the price/Powerwall-charging edges actually occur) lives in
cheap_controller.py, which has to talk to Home Assistant's event loop and
can't be unit tested the same way.
"""
from dataclasses import dataclass
from typing import Optional

ACTION_ENTER_LOW_SOLAR_DAY = "enter_low_solar_day"  # turn go-e's own PV switch off, start suppressing our PV push (and the Tesla's own PV-based gating)
ACTION_EXIT_LOW_SOLAR_DAY = "exit_low_solar_day"  # turn it back on, stop suppressing


@dataclass
class ForecastDecisionInput:
    forecast_kwh: Optional[float]
    threshold_kwh: float


def is_low_solar_day(state: ForecastDecisionInput) -> Optional[bool]:
    """None when the forecast sensor is unavailable - the caller should
    keep whatever the last known decision was rather than guessing."""
    if state.forecast_kwh is None:
        return None
    return state.forecast_kwh < state.threshold_kwh


@dataclass
class PriceWindowInput:
    price: Optional[float]
    threshold_ct: float


def is_cheap_now(state: PriceWindowInput) -> Optional[bool]:
    if state.price is None:
        return None
    return state.price < state.threshold_ct


@dataclass
class DailyRolloverInput:
    was_suppressing: bool  # PV switch/push already off because of us
    low_solar_today: bool  # freshly latched decision for the day just starting


def decide_daily_rollover(state: DailyRolloverInput) -> Optional[str]:
    """Called once, right as the price window opens (= local midnight in
    this setup). Returns the switch/suppression action to take, if any."""
    if state.low_solar_today and not state.was_suppressing:
        return ACTION_ENTER_LOW_SOLAR_DAY
    if not state.low_solar_today and state.was_suppressing:
        return ACTION_EXIT_LOW_SOLAR_DAY
    return None


@dataclass
class CarChargeResult:
    zoe_should_charge: bool
    tesla_should_charge: bool


@dataclass
class WindowEdgeInput:
    entering: bool
    leaving: bool
    low_solar_today: bool
    # None = "connected" sensor not configured / unavailable - don't force
    # a charge start onto a car we don't know is even plugged in.
    car_connected: Optional[bool]
    tesla_configured: bool
    # Read right at the edge, so that a Powerwall already mid-charge when
    # the window opens is accounted for immediately rather than waiting
    # for its own next edge.
    powerwall_charging: bool
    zoe_has_priority: bool


def decide_window_edge(state: WindowEdgeInput) -> Optional[CarChargeResult]:
    """Called on every price-window entering/leaving edge (and reused,
    with entering=True, whenever something that affects the *current*
    target changes mid-window - see cheap_controller.py). Returns the
    target charge state for both cars, or None if this isn't actually an
    edge on a low-solar day (nothing to do - independent of the daily
    rollover action above, though both can fire on the same edge)."""
    if state.entering and state.low_solar_today:
        zoe_wants = bool(state.car_connected)
        tesla_wants = state.tesla_configured
        if state.powerwall_charging and zoe_wants and tesla_wants:
            # Only one car may draw power while the Powerwall itself is
            # charging (up to its own hardware limit) - priority decides
            # which one keeps going.
            if state.zoe_has_priority:
                return CarChargeResult(True, False)
            return CarChargeResult(False, True)
        return CarChargeResult(zoe_wants, tesla_wants)
    if state.leaving and state.low_solar_today:
        return CarChargeResult(False, False)
    return None


@dataclass
class PowerwallChargingEdgeInput:
    powerwall_charging: bool  # newly read value
    was_charging: bool  # previous value
    zoe_charging: bool  # currently actually charging?
    tesla_charging: bool
    zoe_wants: bool  # would be charging if not for this arbitration
    tesla_wants: bool
    zoe_has_priority: bool


def decide_powerwall_arbitration(state: PowerwallChargingEdgeInput) -> Optional[CarChargeResult]:
    """Called whenever the Powerwall's own charging state changes while
    the forced-charge window is already open on a low-solar day. Only
    meaningful when both cars actually want to charge in the first place
    - otherwise there's nothing to arbitrate between. Returns None when
    nothing should change."""
    if not (state.zoe_wants and state.tesla_wants):
        return None

    started = state.powerwall_charging and not state.was_charging
    stopped = state.was_charging and not state.powerwall_charging

    if started and state.zoe_charging and state.tesla_charging:
        if state.zoe_has_priority:
            return CarChargeResult(True, False)
        return CarChargeResult(False, True)

    if stopped and state.zoe_charging != state.tesla_charging:
        # exactly one of the two is currently running - the other one was
        # (presumably) paused for this exact reason, so resume both.
        return CarChargeResult(True, True)

    return None


def status_text(
    *,
    enabled: bool,
    forecast_kwh: Optional[float],
    threshold_kwh: float,
    low_solar_today: bool,
    cheap_now: bool,
    car_connected: Optional[bool],
    tesla_configured: bool,
    zoe_charging: bool,
    tesla_charging: bool,
    powerwall_charging: bool,
    zoe_car_label: str = "Auto Ladelimit",
    tesla_car_label: str = "Tesla",
) -> str:
    if not enabled:
        return "Deaktiviert"
    if not low_solar_today:
        # forecast_kwh is None here whenever today's decision hasn't been
        # latched by a midnight rollover yet (e.g. right after the evening
        # evaluation, or before the very first one since setup) - "today"
        # simply defaults to a normal day until that happens.
        if forecast_kwh is None:
            return "Normaler Tag (noch keine Vorhersage fuer heute uebernommen)"
        return f"Normaler Tag (Vorhersage {forecast_kwh:.1f} kWh >= {threshold_kwh:.0f} kWh)"
    if forecast_kwh is None:
        # Shouldn't happen in practice - low_solar_today can only become
        # true together with a real forecast value - but keep a safe
        # fallback rather than crashing the f-strings below.
        return "Solar-Vorhersage nicht verfuegbar"
    if not cheap_now:
        return (
            f"Guenstigstrom-Tag (Vorhersage {forecast_kwh:.1f} kWh < {threshold_kwh:.0f} kWh) "
            "- wartet auf Guenstigfenster"
        )
    if not zoe_charging and not tesla_charging:
        if car_connected is False and not tesla_configured:
            return "Guenstigfenster aktiv, aber kein Fahrzeug verbunden"
        return "Guenstigfenster aktiv, aber kein Fahrzeug laedt"

    cars = []
    if zoe_charging:
        cars.append(zoe_car_label)
    if tesla_charging:
        cars.append(tesla_car_label)
    text = f"Guenstigfenster aktiv - {' und '.join(cars)} erzwungen"
    if tesla_configured and powerwall_charging and zoe_charging != tesla_charging:
        paused = tesla_car_label if zoe_charging else zoe_car_label
        text += f", {paused} pausiert (Powerwall laedt)"
    return text
