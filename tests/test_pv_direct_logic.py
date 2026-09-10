"""Unit tests for the pure decision logic in pv_direct_logic.py - kept
separate from tests/test_integration.py (which drives the whole thing
through a real HA core) because the hysteresis/clamping edge cases here
are much more precisely and cheaply covered as plain function calls.
"""
from custom_components.go_e_solar_charger.pv_direct_logic import (
    ACTION_RELEASE,
    ACTION_START,
    ACTION_STOP,
    ACTION_UPDATE,
    PvDirectInput,
    evaluate,
)

THRESHOLD = 50.0
EXPORT_OVERRIDE = 3100.0
HYSTERESIS = 400.0
MAX_AMP = 16


def _base(**overrides) -> PvDirectInput:
    defaults = dict(
        enabled=True,
        powerwall_soc=80.0,
        threshold=THRESHOLD,
        solar_w=5000.0,
        grid_w=-2000.0,  # exporting 2000 W
        battery_w=0.0,
        export_override_w=EXPORT_OVERRIDE,
        max_amp=MAX_AMP,
        charging_active=False,
        active_amp=None,
        active_phase=None,
        zoe_force_off_active=False,
        phase_switch_hysteresis_w=HYSTERESIS,
    )
    defaults.update(overrides)
    return PvDirectInput(**defaults)


def test_disabled_releases_when_previously_charging():
    result = evaluate(_base(enabled=False, charging_active=True, active_amp=10, active_phase=1))
    assert result.action == ACTION_RELEASE
    assert result.charging_active is False


def test_disabled_and_already_stopped_is_a_noop():
    result = evaluate(_base(enabled=False))
    assert result.action is None
    assert result.charging_active is False


def test_zoe_force_off_never_touches_frc():
    # Must never release/stop go-e itself here - the Auto charge limit
    # feature already owns frc in this situation (see module docstring).
    result = evaluate(
        _base(zoe_force_off_active=True, charging_active=True, active_amp=10, active_phase=1)
    )
    assert result.action is None
    assert result.charging_active is False


def test_missing_soc_keeps_previous_state():
    result = evaluate(
        _base(powerwall_soc=None, charging_active=True, active_amp=10, active_phase=1)
    )
    assert result.action is None
    assert result.charging_active is True
    assert result.target_amp == 10
    assert result.target_phase == 1


def test_missing_power_values_keeps_previous_state():
    result = evaluate(
        _base(solar_w=None, charging_active=True, active_amp=10, active_phase=1)
    )
    assert result.action is None
    assert result.charging_active is True


def test_below_threshold_stops_when_charging():
    result = evaluate(
        _base(powerwall_soc=30.0, charging_active=True, active_amp=10, active_phase=1)
    )
    assert result.action == ACTION_STOP
    assert result.charging_active is False


def test_below_threshold_and_not_charging_is_a_noop():
    result = evaluate(_base(powerwall_soc=30.0))
    assert result.action is None
    assert result.charging_active is False


def test_export_override_starts_charging_despite_low_soc():
    result = evaluate(_base(powerwall_soc=30.0, grid_w=-4000.0))
    assert result.action == ACTION_START
    assert result.charging_active is True


def test_insufficient_surplus_stops():
    # Already drawing 6 A / 1-phase = 1380 W (added back to the grid
    # figure, see the "assumed car draw" test below) but now actually
    # *importing* 2000 W - net available is -620 W, well under the 1-phase
    # minimum of 1380 W.
    result = evaluate(
        _base(grid_w=2000.0, charging_active=True, active_amp=6, active_phase=1)
    )
    assert result.action == ACTION_STOP
    assert result.charging_active is False


def test_starts_on_one_phase_when_surplus_is_modest():
    # 2000 W available, below the 3-phase-up threshold (4140 + 400 = 4540).
    result = evaluate(_base(grid_w=-2000.0))
    assert result.action == ACTION_START
    assert result.target_phase == 1
    assert result.target_amp == round(2000 / 230)
    assert result.available_power_w == 2000


def test_amp_is_clamped_to_configured_max():
    result = evaluate(_base(grid_w=-4000.0, max_amp=10))
    assert result.target_phase == 1
    assert result.target_amp == 10


def test_starts_on_three_phases_when_surplus_is_large():
    # Comfortably above 3-phase minimum + hysteresis (4140 + 400 = 4540).
    result = evaluate(_base(grid_w=-6000.0))
    assert result.action == ACTION_START
    assert result.target_phase == 3
    assert result.target_amp == round(6000 / (3 * 230))


def test_phase_switch_hysteresis_prevents_flapping_near_boundary():
    # Sitting exactly at the bare 3-phase minimum (4140 W), not yet
    # charging, should NOT switch up to 3 phases - needs to clear it by
    # the full hysteresis margin (>= 4540 W) first.
    result = evaluate(_base(grid_w=-4140.0))
    assert result.target_phase == 1

    # But once already running on 3 phases and drawing exactly that
    # minimum (6 A * 3 * 230 V = 4140 W, with nothing left over), it
    # should NOT switch back down to 1 phase either - it's still at (not
    # below) the minimum, and the hysteresis margin protects that side too.
    result = evaluate(
        _base(grid_w=0.0, charging_active=True, active_amp=6, active_phase=3)
    )
    assert result.action is None
    assert result.target_phase == 3


def test_no_action_when_target_matches_current_state():
    # Already drawing 9 A / 1-phase = 2070 W, with no further export/import
    # on top of that - an equilibrium the decision should reproduce
    # exactly (2070 W / 230 V = 9 A again), so nothing needs to change.
    result = evaluate(
        _base(grid_w=0.0, charging_active=True, active_amp=9, active_phase=1)
    )
    assert result.action is None
    assert result.charging_active is True


def test_amp_change_while_charging_is_an_update_not_a_restart():
    # Already drawing 8 A / 1-phase = 1840 W, plus 2000 W of further
    # export - 3840 W total, still under the 3-phase-up threshold
    # (4540 W), so only the amp should change, not the phase.
    result = evaluate(
        _base(grid_w=-2000.0, charging_active=True, active_amp=8, active_phase=1)
    )
    assert result.action == ACTION_UPDATE
    assert result.target_phase == 1
    # round(3840 / 230) would be 17 A, clamped down to the configured max.
    assert result.target_amp == MAX_AMP


def test_assumed_car_draw_is_added_back_to_export_for_available_power():
    # Already drawing 6 A / 1-phase = 1380 W, and the grid figure (which
    # already reflects that draw) shows only 500 W of further export - so
    # the *total* available for the car is 1380 + 500 = 1880 W, not 500 W.
    result = evaluate(
        _base(grid_w=-500.0, charging_active=True, active_amp=6, active_phase=1)
    )
    assert result.available_power_w == 1880
