"""Pure decision logic for the PV-surplus push feature, kept free of any
Home Assistant imports so it can be unit tested in isolation.

Idea: only hand the go-e Charger's PV-surplus-charging algorithm (pPv/
pGrid/pAkku, see goe_client.push_pv_values) real numbers once the
Powerwall's own battery has reached a configurable state of charge -
below that, the house battery should fill up first rather than solar
surplus going straight into the car. Below the threshold we explicitly
push zeros instead of just staying silent, so go-e can't keep charging
off stale numbers from before the threshold was crossed downward.
"""
from dataclasses import dataclass
from typing import Optional

PPV_KEY = "pPv"
PGRID_KEY = "pGrid"
PAKKU_KEY = "pAkku"


@dataclass
class PvPushInput:
    enabled: bool
    powerwall_soc: Optional[float]
    threshold: float
    solar_w: Optional[float]
    grid_w: Optional[float]
    battery_w: Optional[float]


@dataclass
class PvPushResult:
    status_text: str
    # None means "don't call go-e this cycle" (feature disabled or a source
    # value is missing) - as opposed to an explicit zeroed push, which is a
    # deliberate "no surplus available" signal.
    values: Optional[dict]


def evaluate(state: PvPushInput) -> PvPushResult:
    if not state.enabled:
        return PvPushResult("Deaktiviert", None)

    if state.powerwall_soc is None:
        return PvPushResult("Akkustand der Powerwall nicht verfuegbar", None)

    if state.powerwall_soc < state.threshold:
        return PvPushResult(
            f"Akkustand {state.powerwall_soc:.0f} % < {state.threshold:.0f} % "
            "- keine PV-Freigabe an go-e",
            {PPV_KEY: 0, PGRID_KEY: 0, PAKKU_KEY: 0},
        )

    if state.solar_w is None or state.grid_w is None or state.battery_w is None:
        return PvPushResult("Leistungswerte der Powerwall nicht verfuegbar", None)

    return PvPushResult(
        f"PV-Werte gesendet (Akkustand {state.powerwall_soc:.0f} % >= {state.threshold:.0f} %)",
        {PPV_KEY: state.solar_w, PGRID_KEY: state.grid_w, PAKKU_KEY: state.battery_w},
    )
