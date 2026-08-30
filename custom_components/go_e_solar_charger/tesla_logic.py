"""Pure decision logic for the Tesla charge-gating feature, kept free of
any Home Assistant imports so it can be unit tested in isolation.

The Tesla has its own solar-aware charging solution and manages its own
charge limit (number.tesla_ladelimit, outside this integration's scope) -
all this feature does is start/stop a plain on/off switch for its
charging. Charging is only allowed once the Powerwall has reached the
same SoC threshold configured for the PV-surplus-push feature (see
pv_logic.py, reused live via the controller) - unless enough power is
already being exported to the grid to be worth using regardless of the
Powerwall's own state.
"""
from dataclasses import dataclass
from typing import Optional

# The grid-power reading is noisy even while nothing meaningful is
# happening (a few Watts of jitter around zero net export). Rounding it to
# this granularity before it goes into the status text keeps that text -
# and therefore the sensor's logged state - stable while the underlying
# decision hasn't changed, instead of rewriting (and re-logging) it on
# every single evaluation. The actual gating decision below still compares
# against the *unrounded* value, so this only affects what's displayed.
_DISPLAY_ROUNDING_W = 100


def _rounded_w(value: float) -> float:
    rounded = round(value / _DISPLAY_ROUNDING_W) * _DISPLAY_ROUNDING_W
    return 0.0 if rounded == 0 else rounded


@dataclass
class TeslaChargeInput:
    enabled: bool
    powerwall_soc: Optional[float]
    soc_threshold: float
    grid_w: Optional[float]  # negative = feeding into the grid
    grid_release_threshold_w: float


@dataclass
class TeslaChargeResult:
    status_text: str
    # None = leave the switch alone (feature disabled, or the Powerwall
    # SoC is unknown and we'd rather not guess) - True/False = the switch
    # should be turned on/off.
    should_charge: Optional[bool]


def evaluate(state: TeslaChargeInput) -> TeslaChargeResult:
    if not state.enabled:
        return TeslaChargeResult("Deaktiviert", None)

    if state.powerwall_soc is None:
        return TeslaChargeResult("Akkustand der Powerwall nicht verfuegbar", None)

    if state.powerwall_soc >= state.soc_threshold:
        return TeslaChargeResult(
            f"Laden freigegeben (Akkustand {state.powerwall_soc:.0f} % >= "
            f"{state.soc_threshold:.0f} %)",
            True,
        )

    export_w = None if state.grid_w is None else -state.grid_w
    if export_w is not None and export_w >= state.grid_release_threshold_w:
        return TeslaChargeResult(
            f"Laden freigegeben (Einspeisung {_rounded_w(export_w):.0f} W >= "
            f"{state.grid_release_threshold_w:.0f} W trotz Akkustand "
            f"{state.powerwall_soc:.0f} % < {state.soc_threshold:.0f} %)",
            True,
        )

    export_text = "unbekannt" if export_w is None else f"{_rounded_w(export_w):.0f} W"
    return TeslaChargeResult(
        f"Laden gestoppt (Akkustand {state.powerwall_soc:.0f} % < "
        f"{state.soc_threshold:.0f} %, Einspeisung {export_text} < "
        f"{state.grid_release_threshold_w:.0f} W)",
        False,
    )
