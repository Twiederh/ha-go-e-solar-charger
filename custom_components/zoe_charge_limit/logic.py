"""Pure decision logic for the charge limit, kept free of any Home Assistant
imports so it can be unit tested in isolation.

The physical model this relies on: once we send the go-e a forced "Off"
(frc=1), charging actually stops, so the car's reported SoC will not keep
climbing on its own - it only comes back down below the limit because the
user raised the limit, or (very slowly) through self-discharge while
parked. Either way, "SoC now below the limit" is exactly the signal to
release the forced stop again, regardless of why it happened.
"""
from dataclasses import dataclass
from typing import Optional

ACTION_STOP = "stop"
ACTION_RESET = "reset"


@dataclass
class ChargeLimitInput:
    enabled: bool
    charging: bool
    # None when no "car connected" entity was configured - falls back to
    # using `charging` itself as the presence signal.
    car_connected: Optional[bool]
    soc: Optional[float]
    limit: float
    # Whether *we* currently believe the go-e is force-stopped because of
    # this integration (not because of e.g. a manual force-off by the user
    # via the go-e app - we don't try to detect or fight that).
    force_off_active: bool


@dataclass
class ChargeLimitResult:
    status_text: str
    force_off_active: bool
    action: Optional[str]  # None, ACTION_STOP or ACTION_RESET


def evaluate(state: ChargeLimitInput) -> ChargeLimitResult:
    if not state.enabled:
        action = ACTION_RESET if state.force_off_active else None
        return ChargeLimitResult("Deaktiviert", False, action)

    car_present = state.car_connected if state.car_connected is not None else state.charging
    if not car_present:
        action = ACTION_RESET if state.force_off_active else None
        return ChargeLimitResult("Kein Fahrzeug verbunden", False, action)

    if state.soc is None:
        # Don't guess - keep whatever we were doing until the sensor comes
        # back, so a transient "unavailable" can't accidentally resume a
        # charge that should stay stopped.
        status = "Ladezustand nicht verfuegbar"
        return ChargeLimitResult(status, state.force_off_active, None)

    if state.soc >= state.limit:
        status = (
            f"Ladelimit aktiv ({state.limit:.0f} %) - Laden gestoppt"
            if state.force_off_active
            else f"Ladelimit erreicht ({state.soc:.0f} % >= {state.limit:.0f} %) - Laden gestoppt"
        )
        action = None if state.force_off_active else ACTION_STOP
        return ChargeLimitResult(status, True, action)

    # SoC is below the limit.
    if state.force_off_active:
        # The limit was raised, or the car lost some charge while parked -
        # either way there's headroom again, so let go-e resume normally.
        status = f"Laedt ({state.soc:.0f} % / Limit {state.limit:.0f} %)" if state.charging \
            else f"Bereit (SoC {state.soc:.0f} %, Limit {state.limit:.0f} %)"
        return ChargeLimitResult(status, False, ACTION_RESET)

    if state.charging:
        return ChargeLimitResult(
            f"Laedt ({state.soc:.0f} % / Limit {state.limit:.0f} %)", False, None
        )

    return ChargeLimitResult(
        f"Bereit (SoC {state.soc:.0f} %, Limit {state.limit:.0f} %)", False, None
    )
