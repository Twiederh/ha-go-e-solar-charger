"""Unit tests for the pure decision logic in pv_logic.py - kept separate
from tests/test_integration.py (which drives the whole thing through a
real HA core) for the same reason as test_pv_direct_logic.py: precise,
cheap coverage of the gating edge cases as plain function calls.
"""
from custom_components.go_e_solar_charger.pv_logic import (
    MIN_SURPLUS_W,
    PAKKU_KEY,
    PGRID_KEY,
    PPV_KEY,
    PvPushInput,
    evaluate,
)

THRESHOLD = 50.0
EXPORT_OVERRIDE = 3100.0


def _base(**overrides) -> PvPushInput:
    defaults = dict(
        enabled=True,
        powerwall_soc=80.0,
        threshold=THRESHOLD,
        solar_w=5000.0,
        grid_w=-2000.0,  # exporting 2000 W
        battery_w=0.0,
        export_override_w=EXPORT_OVERRIDE,
    )
    defaults.update(overrides)
    return PvPushInput(**defaults)


def test_disabled_sends_nothing():
    result = evaluate(_base(enabled=False))
    assert result.values is None


def test_missing_soc_sends_nothing():
    result = evaluate(_base(powerwall_soc=None))
    assert result.values is None


def test_below_threshold_without_override_sends_nothing():
    result = evaluate(_base(powerwall_soc=30.0))
    assert result.values is None
    assert "keine PV-Freigabe" in result.status_text


def test_missing_power_values_sends_nothing():
    result = evaluate(_base(solar_w=None))
    assert result.values is None


def test_above_threshold_with_sufficient_surplus_sends_real_values():
    # 2000 W export, comfortably above the 1380 W (6 A / 230 V) minimum.
    result = evaluate(_base(grid_w=-2000.0))
    assert result.values == {PPV_KEY: 5000.0, PGRID_KEY: -2000.0, PAKKU_KEY: 0.0}
    assert "PV-Werte gesendet" in result.status_text


def test_above_threshold_but_insufficient_surplus_sends_nothing():
    # Reported in practice: SoC above the threshold used to be the *only*
    # condition - a mere 200 W of export (nowhere near enough for go-e to
    # usefully charge with) still got forwarded as real values, and go-e
    # started charging on essentially no surplus at all. Must now send
    # nothing at all instead, exactly like the below-threshold case - see
    # the module docstring for why an explicit zeroed push isn't used here
    # either (go-e kept an already-running charge going regardless).
    result = evaluate(_base(grid_w=-200.0))
    assert result.values is None
    assert "keine PV-Freigabe" in result.status_text
    assert f"{MIN_SURPLUS_W:.0f}" in result.status_text


def test_above_threshold_with_no_export_at_all_sends_nothing():
    # Net importing (positive grid_w) while SoC is above the threshold -
    # obviously no surplus to forward.
    result = evaluate(_base(grid_w=1000.0))
    assert result.values is None


def test_surplus_exactly_at_the_minimum_is_sufficient():
    # The boundary itself must still count as "enough" (strict < below it,
    # not <=) - consistent with pv_direct_logic.py's identical minimum.
    result = evaluate(_base(grid_w=-MIN_SURPLUS_W))
    assert result.values == {PPV_KEY: 5000.0, PGRID_KEY: -float(MIN_SURPLUS_W), PAKKU_KEY: 0.0}


def test_export_override_with_sufficient_surplus_sends_real_values():
    # Well above both the override threshold and the plain minimum.
    result = evaluate(_base(powerwall_soc=30.0, grid_w=-4000.0))
    assert result.values == {PPV_KEY: 5000.0, PGRID_KEY: -4000.0, PAKKU_KEY: 0.0}
    assert "PV-Werte trotzdem gesendet" in result.status_text


def test_export_override_below_the_plain_minimum_still_sends_nothing():
    # Defensive edge case: a misconfigured export_override_w lower than
    # MIN_SURPLUS_W must not let through an amount go-e can't do anything
    # useful with just because it happens to clear that (too low) override.
    result = evaluate(
        _base(powerwall_soc=30.0, grid_w=-1000.0, export_override_w=500.0)
    )
    assert result.values is None
    assert "keine PV-Freigabe" in result.status_text
