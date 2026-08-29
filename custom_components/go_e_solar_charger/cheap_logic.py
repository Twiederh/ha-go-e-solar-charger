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

This module only computes the two booleans and the resulting actions/
status text - all the timing (when to sample the forecast, when the price
edges actually occur) lives in cheap_controller.py, which has to talk to
Home Assistant's event loop and can't be unit tested the same way.
"""
from dataclasses import dataclass
from typing import Optional

ACTION_ENTER_LOW_SOLAR_DAY = "enter_low_solar_day"  # turn go-e's own PV switch off, start suppressing our PV push
ACTION_EXIT_LOW_SOLAR_DAY = "exit_low_solar_day"  # turn it back on, stop suppressing
ACTION_START_FORCED_CHARGE = "start_forced_charge"  # frc=On
ACTION_STOP_FORCED_CHARGE = "stop_forced_charge"  # frc=Neutral


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
class WindowEdgeInput:
    entering: bool
    leaving: bool
    low_solar_today: bool
    # None = "connected" sensor not configured / unavailable - don't force
    # a charge start onto a car we don't know is even plugged in.
    car_connected: Optional[bool]


def decide_window_edge(state: WindowEdgeInput) -> Optional[str]:
    """Called on every price-window entering/leaving edge. Returns the
    forced-charge action to take, if any - independent of the daily
    rollover action above (both can fire on the same entering edge)."""
    if state.entering and state.low_solar_today and state.car_connected:
        return ACTION_START_FORCED_CHARGE
    if state.leaving and state.low_solar_today:
        return ACTION_STOP_FORCED_CHARGE
    return None


def status_text(
    *,
    enabled: bool,
    forecast_kwh: Optional[float],
    threshold_kwh: float,
    low_solar_today: bool,
    cheap_now: bool,
    forced_active: bool,
    car_connected: Optional[bool],
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
    if forced_active:
        return (
            f"Guenstigfenster aktiv - Laden erzwungen "
            f"(Vorhersage {forecast_kwh:.1f} kWh < {threshold_kwh:.0f} kWh)"
        )
    if cheap_now and car_connected is False:
        return "Guenstigfenster aktiv, aber kein Fahrzeug verbunden"
    return (
        f"Guenstigstrom-Tag (Vorhersage {forecast_kwh:.1f} kWh < {threshold_kwh:.0f} kWh) "
        "- wartet auf Guenstigfenster"
    )
