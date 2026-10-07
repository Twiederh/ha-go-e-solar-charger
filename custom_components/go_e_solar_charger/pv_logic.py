"""Pure decision logic for the PV-surplus push feature, kept free of any
Home Assistant imports so it can be unit tested in isolation.

Idea: only hand the go-e Charger's PV-surplus-charging algorithm (pPv/
pGrid/pAkku, see goe_client.push_pv_values) real numbers once the
Powerwall's own battery has reached a configurable state of charge -
below that, the house battery should fill up first rather than solar
surplus going straight into the car.

Exception: the Powerwall itself sometimes exports a lot of power even
while still below its own SoC threshold - e.g. around midday in summer,
to avoid sitting at 100 % for too long. Once that export exceeds a
configurable override threshold, real values are forwarded anyway rather
than wasting the surplus.

Reported in practice: the SoC gate above was the *only* condition ever
checked before forwarding real values - once the Powerwall was at/above
its threshold, whatever pPv/pGrid/pAkku currently read got sent through
unconditionally, with no check at all on whether there was actually
enough surplus to charge from. go-e's own PV-surplus algorithm apparently
doesn't reliably refuse a near-zero/negative surplus on its own either
(same lesson as pv_direct_logic.py's module docstring about not trusting
go-e's own logic to behave conservatively) - it was observed to start
charging the car even while the house was, net, not exporting anything
worth mentioning. Real values are therefore now only forwarded once the
export also clears MIN_SURPLUS_W (the same 6 A/230 V single-phase floor
pv_direct_logic.py uses - the smallest amount go-e can do anything useful
with).

Below the threshold (or once the surplus is insufficient), earlier
versions explicitly pushed zeros instead of just staying silent, on the
theory that go-e might otherwise keep charging off stale real numbers
from before the surplus disappeared. Reported in practice, that didn't
work either: with zeros being actively (re-)sent every
PV_PUSH_KEEPALIVE_INTERVAL_SECONDS, go-e kept an already-running charge
going regardless - its own PV-surplus algorithm apparently treats "a
fresh reading of 0 W" as just another surplus value to ramp towards
(presumably floored at its own hardware minimum current) rather than a
stop signal. go-e *is* documented and relied upon elsewhere (see
PV_PUSH_KEEPALIVE_INTERVAL_SECONDS's own comment) to pause charging as a
safety fallback once it hasn't seen an ids update for a few seconds - so
the integration now instead sends nothing at all whenever the surplus
isn't sufficient (SoC below threshold, or export below MIN_SURPLUS_W),
and lets that existing staleness safeguard do the actual stopping,
instead of hoping go-e reacts to an explicit 0/0/0.
"""
from dataclasses import dataclass
from typing import Optional

# See tesla_logic.py's identical constant/helper for the rationale: this
# only smooths the *displayed* export value so the status text (and thus
# the sensor's logged state) doesn't get rewritten on every evaluation
# purely from grid-meter noise - the override decision itself still
# compares against the unrounded value.
_DISPLAY_ROUNDING_W = 100

# Same floor as pv_direct_logic.py's MIN_AMP * VOLTAGE_V (6 A on a single
# phase) - below this, go-e can't usefully do anything with the surplus
# anyway, so there's no point forwarding real values and letting go-e's
# own algorithm decide (see module docstring).
MIN_SURPLUS_W = 6 * 230


def _rounded_w(value: float) -> float:
    rounded = round(value / _DISPLAY_ROUNDING_W) * _DISPLAY_ROUNDING_W
    return 0.0 if rounded == 0 else rounded


PPV_KEY = "pPv"
PGRID_KEY = "pGrid"
PAKKU_KEY = "pAkku"


@dataclass
class PvPushInput:
    enabled: bool
    powerwall_soc: Optional[float]
    threshold: float
    solar_w: Optional[float]
    grid_w: Optional[float]  # negative = feeding into the grid
    battery_w: Optional[float]
    export_override_w: float


@dataclass
class PvPushResult:
    status_text: str
    # None means "don't call go-e this cycle at all" - feature disabled, a
    # source value missing, or (see module docstring) surplus insufficient:
    # go-e's own ids-staleness safety pause is what actually stops the
    # charge in that last case, not an explicit zeroed push.
    values: Optional[dict]


def evaluate(state: PvPushInput) -> PvPushResult:
    if not state.enabled:
        return PvPushResult("Deaktiviert", None)

    if state.powerwall_soc is None:
        return PvPushResult("Akkustand der Powerwall nicht verfuegbar", None)

    below_threshold = state.powerwall_soc < state.threshold
    export_w = None if state.grid_w is None else -state.grid_w
    export_override = (
        below_threshold and export_w is not None and export_w > state.export_override_w
    )

    if below_threshold and not export_override:
        return PvPushResult(
            f"Akkustand {state.powerwall_soc:.0f} % < {state.threshold:.0f} % "
            "- keine PV-Freigabe an go-e",
            None,
        )

    if state.solar_w is None or state.grid_w is None or state.battery_w is None:
        return PvPushResult("Leistungswerte der Powerwall nicht verfuegbar", None)

    # Reported in practice: neither branch below used to check this at all -
    # whatever the SoC/export-override gate decided, the *current* export
    # amount was irrelevant to whether real values got sent. See module
    # docstring: go-e's own algorithm can't be trusted to politely decline a
    # near-zero/negative surplus just because it was handed one.
    insufficient_surplus = export_w is None or export_w < MIN_SURPLUS_W

    if export_override:
        if insufficient_surplus:
            return PvPushResult(
                f"Einspeisung {_rounded_w(export_w):.0f} W < Minimum {MIN_SURPLUS_W:.0f} W "
                f"(trotz Akkustand {state.powerwall_soc:.0f} % < {state.threshold:.0f} %) "
                "- keine PV-Freigabe an go-e",
                None,
            )
        return PvPushResult(
            f"Einspeisung {_rounded_w(export_w):.0f} W > {state.export_override_w:.0f} W trotz "
            f"Akkustand {state.powerwall_soc:.0f} % < {state.threshold:.0f} % "
            "- PV-Werte trotzdem gesendet",
            {PPV_KEY: state.solar_w, PGRID_KEY: state.grid_w, PAKKU_KEY: state.battery_w},
        )

    if insufficient_surplus:
        return PvPushResult(
            f"Ueberschuss {_rounded_w(export_w):.0f} W < Minimum {MIN_SURPLUS_W:.0f} W "
            f"(Akkustand {state.powerwall_soc:.0f} % >= {state.threshold:.0f} %) "
            "- keine PV-Freigabe an go-e",
            None,
        )

    return PvPushResult(
        f"PV-Werte gesendet (Akkustand {state.powerwall_soc:.0f} % >= {state.threshold:.0f} %)",
        {PPV_KEY: state.solar_w, PGRID_KEY: state.grid_w, PAKKU_KEY: state.battery_w},
    )
