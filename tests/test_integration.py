"""End-to-end test of the integration against a real (test-mode) Home
Assistant core: sets up the config entry, lets all entities register, then
drives the source sensors and checks both features call a mocked go-e
correctly and update their status sensors.

Not part of the shipped custom_component - a development-time check.
"""
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from homeassistant.util import dt as dt_util

from custom_components.go_e_solar_charger.const import (
    CHEAP_FORECAST_EVAL_HOUR,
    CHEAP_FORECAST_EVAL_MINUTE,
    CHEAP_PRIORITY_AUTO_FIRST,
    CHEAP_PRIORITY_TESLA_FIRST,
    DOMAIN,
    PV_PUSH_KEEPALIVE_INTERVAL_SECONDS,
)

ZOE_SOC_ENTITY = "sensor.zoe_batterie_soc"
ZOE_CHARGING_ENTITY = "binary_sensor.goe_charging"
ZOE_CONNECTED_ENTITY = "binary_sensor.goe_car_connected"

PV_SOLAR_ENTITY = "sensor.powerwall_solar_power"
PV_GRID_ENTITY = "sensor.powerwall_grid_power"
PV_BATTERY_ENTITY = "sensor.powerwall_battery_power"
PV_SOC_ENTITY = "sensor.powerwall_soc"

CHEAP_FORECAST_ENTITY = "sensor.solar_forecast_tomorrow"
CHEAP_PRICE_ENTITY = "sensor.octopus_a_eff1657d_electricity_price"
CHEAP_GOE_PV_SWITCH_ENTITY = "switch.goe_wan_213832_fup"
CHEAP_PRICE_EXPENSIVE = "26.667"
CHEAP_PRICE_CHEAP = "16.667"

TESLA_SWITCH_ENTITY = "switch.tesla_aufladung"

DEVICE_SLUG = "go_e_solar_charger"


async def _make_entry(hass, **overrides):
    data = {
        "goe_host": "127.0.0.1:1",  # unused when go-e calls are mocked
        "goe_api_key": "",
        "zoe_soc_entity_id": ZOE_SOC_ENTITY,
        "zoe_charging_entity_id": ZOE_CHARGING_ENTITY,
        "zoe_charging_on_state": "on",
        "zoe_car_connected_entity_id": ZOE_CONNECTED_ENTITY,
        "zoe_car_connected_on_state": "on",
        "zoe_default_limit": 80,
        "pv_solar_entity_id": PV_SOLAR_ENTITY,
        "pv_grid_entity_id": PV_GRID_ENTITY,
        "pv_battery_entity_id": PV_BATTERY_ENTITY,
        "pv_soc_entity_id": PV_SOC_ENTITY,
        "pv_default_threshold": 50,
        "pv_export_override_threshold_w": 3100,
        "cheap_forecast_entity_id": CHEAP_FORECAST_ENTITY,
        "cheap_price_entity_id": CHEAP_PRICE_ENTITY,
        "cheap_goe_pv_switch_entity_id": CHEAP_GOE_PV_SWITCH_ENTITY,
        "cheap_forecast_threshold_kwh": 30,
        "cheap_price_threshold_ct": 20,
        "tesla_switch_entity_id": TESLA_SWITCH_ENTITY,
        "tesla_grid_release_threshold_w": 1400,
    }
    data.update(overrides)
    entry = MockConfigEntry(domain=DOMAIN, data=data, title="go-e Solar Charger")
    entry.add_to_hass(hass)
    return entry


def _state(hass, entity_id):
    s = hass.states.get(entity_id)
    return s.state if s else None


def _fire_evening_eval(hass, day_offset=0):
    """`async_track_time_change` schedules its *next* occurrence relative to
    real wall-clock time at registration - today's eval time if that's
    still ahead of now, otherwise tomorrow's. Compute the same way here
    (instead of always assuming "today"), or this flakes depending on what
    time of day the test happens to run. `async_track_time_change` then
    schedules its following occurrence 24h after whatever actually fired,
    so each subsequent call in a test needs its own day_offset to land on
    the occurrence the tracker is now waiting for."""
    now = dt_util.now()
    target = now.replace(
        hour=CHEAP_FORECAST_EVAL_HOUR, minute=CHEAP_FORECAST_EVAL_MINUTE, second=0, microsecond=0
    )
    if target <= now:
        target += timedelta(days=1)
    target += timedelta(days=day_offset)
    async_fire_time_changed(hass, target)


@pytest.mark.asyncio
async def test_zoe_charge_limit_flow(hass, enable_custom_integrations):
    hass.states.async_set(ZOE_SOC_ENTITY, "50")
    hass.states.async_set(ZOE_CHARGING_ENTITY, "on")
    hass.states.async_set(ZOE_CONNECTED_ENTITY, "on")
    hass.states.async_set(PV_SOC_ENTITY, "10")  # keep PV feature quiet (zeros) for this test
    hass.states.async_set(PV_SOLAR_ENTITY, "0")
    hass.states.async_set(PV_GRID_ENTITY, "0")
    hass.states.async_set(PV_BATTERY_ENTITY, "0")

    with patch(
        "custom_components.go_e_solar_charger.goe_client.GoEClient.stop_charging",
        new=AsyncMock(),
    ) as mock_stop, patch(
        "custom_components.go_e_solar_charger.goe_client.GoEClient.release",
        new=AsyncMock(),
    ) as mock_release, patch(
        "custom_components.go_e_solar_charger.goe_client.GoEClient.push_pv_values",
        new=AsyncMock(),
    ):
        entry = await _make_entry(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert hass.states.get(f"number.{DEVICE_SLUG}_auto_ladelimit") is not None
        assert hass.states.get(f"switch.{DEVICE_SLUG}_auto_ladelimit_aktiviert") is not None
        assert hass.states.get(f"sensor.{DEVICE_SLUG}_auto_ladelimit_status") is not None
        assert hass.states.get(f"button.{DEVICE_SLUG}_laden_jetzt_stoppen") is not None

        assert "Laedt" in _state(hass, f"sensor.{DEVICE_SLUG}_auto_ladelimit_status")
        assert mock_stop.call_count == 0

        hass.states.async_set(ZOE_SOC_ENTITY, "80")
        await hass.async_block_till_done()
        assert mock_stop.call_count == 1
        assert "gestoppt" in _state(hass, f"sensor.{DEVICE_SLUG}_auto_ladelimit_status")

        hass.states.async_set(ZOE_SOC_ENTITY, "81")
        await hass.async_block_till_done()
        assert mock_stop.call_count == 1  # no repeat while still above limit

        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": f"number.{DEVICE_SLUG}_auto_ladelimit", "value": 90},
            blocking=True,
        )
        await hass.async_block_till_done()
        assert mock_release.call_count == 1

        await hass.services.async_call(
            "button",
            "press",
            {"entity_id": f"button.{DEVICE_SLUG}_laden_jetzt_stoppen"},
            blocking=True,
        )
        await hass.async_block_till_done()
        assert mock_stop.call_count == 2
        assert _state(hass, f"sensor.{DEVICE_SLUG}_auto_ladelimit_status") == "Manuell gestoppt"


@pytest.mark.asyncio
async def test_pv_surplus_push_flow(hass, enable_custom_integrations):
    hass.states.async_set(ZOE_SOC_ENTITY, "50")
    hass.states.async_set(ZOE_CHARGING_ENTITY, "off")
    hass.states.async_set(ZOE_CONNECTED_ENTITY, "off")

    hass.states.async_set(PV_SOC_ENTITY, "30")  # below the 50% threshold
    hass.states.async_set(PV_SOLAR_ENTITY, "3000")
    hass.states.async_set(PV_GRID_ENTITY, "-200")
    hass.states.async_set(PV_BATTERY_ENTITY, "-500")

    with patch(
        "custom_components.go_e_solar_charger.goe_client.GoEClient.stop_charging",
        new=AsyncMock(),
    ), patch(
        "custom_components.go_e_solar_charger.goe_client.GoEClient.release",
        new=AsyncMock(),
    ), patch(
        "custom_components.go_e_solar_charger.goe_client.GoEClient.push_pv_values",
        new=AsyncMock(),
    ) as mock_push:
        entry = await _make_entry(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert hass.states.get(f"number.{DEVICE_SLUG}_pv_freigabe_ab_akkustand") is not None
        assert hass.states.get(f"switch.{DEVICE_SLUG}_pv_freigabe_aktiviert") is not None
        assert hass.states.get(f"sensor.{DEVICE_SLUG}_pv_freigabe_status") is not None
        assert hass.states.get(f"button.{DEVICE_SLUG}_pv_jetzt_senden") is not None

        # below threshold -> zeros pushed, not the real 3000/-200/-500
        assert mock_push.call_count >= 1
        assert mock_push.call_args.args[0] == {"pPv": 0, "pGrid": 0, "pAkku": 0}
        assert "keine PV-Freigabe" in _state(hass, f"sensor.{DEVICE_SLUG}_pv_freigabe_status")

        # the sensor's attributes must show both what was actually read
        # from the source sensors (the real 3000/-200/-500/30) and what
        # was actually sent (the safety zeros) - so a mismatch between
        # "what go-e should be getting" and "what it's really getting" can
        # be checked directly in the UI instead of just trusting the
        # status text.
        attrs = hass.states.get(f"sensor.{DEVICE_SLUG}_pv_freigabe_status").attributes
        assert attrs["gelesen_solar_w"] == 3000.0
        assert attrs["gelesen_netz_w"] == -200.0
        assert attrs["gelesen_akku_w"] == -500.0
        assert attrs["gelesen_powerwall_soc"] == 30.0
        assert attrs["gesendet_pPv"] == 0
        assert attrs["gesendet_pGrid"] == 0
        assert attrs["gesendet_pAkku"] == 0

        # cross the threshold -> real values pushed
        hass.states.async_set(PV_SOC_ENTITY, "70")
        await hass.async_block_till_done()
        await hass.services.async_call(
            "button",
            "press",
            {"entity_id": f"button.{DEVICE_SLUG}_pv_jetzt_senden"},
            blocking=True,
        )
        await hass.async_block_till_done()
        assert mock_push.call_args.args[0] == {"pPv": 3000.0, "pGrid": -200.0, "pAkku": -500.0}
        assert "PV-Werte gesendet" in _state(hass, f"sensor.{DEVICE_SLUG}_pv_freigabe_status")

        attrs = hass.states.get(f"sensor.{DEVICE_SLUG}_pv_freigabe_status").attributes
        assert attrs["gesendet_pPv"] == 3000.0
        assert attrs["gesendet_pGrid"] == -200.0
        assert attrs["gesendet_pAkku"] == -500.0
        assert attrs["letzte_uebertragung"] is not None

        # lower the threshold above the current SoC again via the number
        # entity -> back to zeros
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": f"number.{DEVICE_SLUG}_pv_freigabe_ab_akkustand", "value": 90},
            blocking=True,
        )
        await hass.async_block_till_done()
        assert mock_push.call_args.args[0] == {"pPv": 0, "pGrid": 0, "pAkku": 0}

        # disabling the switch stops pushing anything at all
        await hass.services.async_call(
            "switch",
            "turn_off",
            {"entity_id": f"switch.{DEVICE_SLUG}_pv_freigabe_aktiviert"},
            blocking=True,
        )
        await hass.async_block_till_done()
        assert _state(hass, f"sensor.{DEVICE_SLUG}_pv_freigabe_status") == "Deaktiviert"
        calls_before = mock_push.call_count
        hass.states.async_set(PV_SOLAR_ENTITY, "5000")
        await hass.async_block_till_done()
        assert mock_push.call_count == calls_before


@pytest.mark.asyncio
async def test_restores_state_after_reload(hass, enable_custom_integrations):
    hass.states.async_set(ZOE_SOC_ENTITY, "50")
    hass.states.async_set(ZOE_CHARGING_ENTITY, "on")
    hass.states.async_set(ZOE_CONNECTED_ENTITY, "on")
    hass.states.async_set(PV_SOC_ENTITY, "10")
    hass.states.async_set(PV_SOLAR_ENTITY, "0")
    hass.states.async_set(PV_GRID_ENTITY, "0")
    hass.states.async_set(PV_BATTERY_ENTITY, "0")

    with patch(
        "custom_components.go_e_solar_charger.goe_client.GoEClient.stop_charging",
        new=AsyncMock(),
    ), patch(
        "custom_components.go_e_solar_charger.goe_client.GoEClient.release",
        new=AsyncMock(),
    ), patch(
        "custom_components.go_e_solar_charger.goe_client.GoEClient.push_pv_values",
        new=AsyncMock(),
    ):
        entry = await _make_entry(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": f"number.{DEVICE_SLUG}_auto_ladelimit", "value": 65},
            blocking=True,
        )
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": f"number.{DEVICE_SLUG}_pv_freigabe_ab_akkustand", "value": 40},
            blocking=True,
        )
        await hass.services.async_call(
            "switch",
            "turn_off",
            {"entity_id": f"switch.{DEVICE_SLUG}_auto_ladelimit_aktiviert"},
            blocking=True,
        )
        await hass.async_block_till_done()

        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert float(_state(hass, f"number.{DEVICE_SLUG}_auto_ladelimit")) == 65
        assert float(_state(hass, f"number.{DEVICE_SLUG}_pv_freigabe_ab_akkustand")) == 40
        assert _state(hass, f"switch.{DEVICE_SLUG}_auto_ladelimit_aktiviert") == "off"


@pytest.mark.asyncio
async def test_pv_keepalive_resends_without_sensor_change(hass, enable_custom_integrations):
    """go-e pauses charging if pPv/pGrid/pAkku aren't refreshed for a few
    seconds - so the controller must keep re-sending on a timer even when
    none of the source sensors change at all."""
    hass.states.async_set(ZOE_SOC_ENTITY, "50")
    hass.states.async_set(ZOE_CHARGING_ENTITY, "off")
    hass.states.async_set(ZOE_CONNECTED_ENTITY, "off")

    hass.states.async_set(PV_SOC_ENTITY, "70")  # above the 50 % threshold
    hass.states.async_set(PV_SOLAR_ENTITY, "3000")
    hass.states.async_set(PV_GRID_ENTITY, "-200")
    hass.states.async_set(PV_BATTERY_ENTITY, "-500")

    with patch(
        "custom_components.go_e_solar_charger.goe_client.GoEClient.stop_charging",
        new=AsyncMock(),
    ), patch(
        "custom_components.go_e_solar_charger.goe_client.GoEClient.release",
        new=AsyncMock(),
    ), patch(
        "custom_components.go_e_solar_charger.goe_client.GoEClient.push_pv_values",
        new=AsyncMock(),
    ) as mock_push:
        entry = await _make_entry(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        calls_after_setup = mock_push.call_count
        assert calls_after_setup >= 1
        assert mock_push.call_args.args[0] == {"pPv": 3000.0, "pGrid": -200.0, "pAkku": -500.0}

        # No sensor changes at all - just let the keep-alive timer fire.
        async_fire_time_changed(
            hass, dt_util.utcnow() + timedelta(seconds=PV_PUSH_KEEPALIVE_INTERVAL_SECONDS + 1)
        )
        await hass.async_block_till_done()

        assert mock_push.call_count > calls_after_setup
        assert mock_push.call_args.args[0] == {"pPv": 3000.0, "pGrid": -200.0, "pAkku": -500.0}


@pytest.mark.asyncio
async def test_cheap_daily_cycle_and_pv_suppression(hass, enable_custom_integrations):
    """Full day-ahead cycle: a poor forecast for "tomorrow" gets latched in
    the evening, rolls over into "today" right as the price window opens at
    midnight (switch off, PV push suppressed, charge forced on while a car
    is connected), forced charging stops again when the window closes but
    the switch/suppression stay off for the rest of that day, and finally
    lift again at the *next* midnight once a fresh (good) forecast rolls
    over."""
    hass.states.async_set(ZOE_SOC_ENTITY, "50")
    hass.states.async_set(ZOE_CHARGING_ENTITY, "off")
    hass.states.async_set(ZOE_CONNECTED_ENTITY, "on")
    hass.states.async_set(PV_SOC_ENTITY, "70")  # would normally be actively pushing
    hass.states.async_set(PV_SOLAR_ENTITY, "3000")
    hass.states.async_set(PV_GRID_ENTITY, "-200")
    hass.states.async_set(PV_BATTERY_ENTITY, "-500")

    hass.states.async_set(CHEAP_FORECAST_ENTITY, "18")  # below the 30 kWh threshold
    hass.states.async_set(CHEAP_PRICE_ENTITY, CHEAP_PRICE_EXPENSIVE)
    hass.states.async_set(CHEAP_GOE_PV_SWITCH_ENTITY, "on")

    with patch(
        "custom_components.go_e_solar_charger.goe_client.GoEClient.stop_charging",
        new=AsyncMock(),
    ), patch(
        "custom_components.go_e_solar_charger.goe_client.GoEClient.release",
        new=AsyncMock(),
    ) as mock_release, patch(
        "custom_components.go_e_solar_charger.goe_client.GoEClient.force_charging_on",
        new=AsyncMock(),
    ) as mock_force_on, patch(
        "custom_components.go_e_solar_charger.goe_client.GoEClient.push_pv_values",
        new=AsyncMock(),
    ) as mock_push, patch(
        "custom_components.go_e_solar_charger.cheap_controller.CheapGridChargingController._set_goe_pv_switch",
        new=AsyncMock(),
    ) as mock_set_switch:
        entry = await _make_entry(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        controllers = hass.data[DOMAIN][entry.entry_id]
        pv_controller = controllers["pv"]

        assert hass.states.get(f"number.{DEVICE_SLUG}_guenstigstrom_solar_schwelle") is not None
        assert hass.states.get(f"number.{DEVICE_SLUG}_guenstigstrom_preis_schwelle") is not None
        assert hass.states.get(f"switch.{DEVICE_SLUG}_guenstigstrom_aktiviert") is not None
        assert hass.states.get(f"sensor.{DEVICE_SLUG}_guenstigstrom_status") is not None
        assert hass.states.get(f"button.{DEVICE_SLUG}_guenstigstrom_jetzt_testen") is not None

        # A forecast for "tomorrow" was already available before setup even
        # ran (the normal case after a restart/reload, or simply because
        # the forecast sensor already had data at install time) - today's
        # low-solar decision, and the suppression that goes with it, must
        # already be in effect right away rather than waiting for the
        # scheduled evening evaluation.
        assert mock_set_switch.call_args.args == (False,)
        assert "wartet auf Guenstigfenster" in _state(
            hass, f"sensor.{DEVICE_SLUG}_guenstigstrom_status"
        )

        # Evening: re-latches tomorrow's decision from the (unchanged)
        # forecast - today's was already latched at setup, so nothing
        # changes yet.
        _fire_evening_eval(hass)
        await hass.async_block_till_done()
        assert "wartet auf Guenstigfenster" in _state(
            hass, f"sensor.{DEVICE_SLUG}_guenstigstrom_status"
        )

        # Midnight: price drops -> forced charge starts (the switch/
        # suppression were already applied at setup, so no new switch call
        # happens here, only the forced charge).
        hass.states.async_set(CHEAP_PRICE_ENTITY, CHEAP_PRICE_CHEAP)
        await hass.async_block_till_done()

        assert mock_set_switch.call_args.args == (False,)
        assert mock_force_on.call_count == 1
        assert "erzwungen" in _state(hass, f"sensor.{DEVICE_SLUG}_guenstigstrom_status")

        # PV-surplus push must be fully suppressed now, even though its own
        # sensors would normally have it actively pushing real values -
        # calls before this point (from setup, with PV_SOC already above
        # its own threshold) don't count, only that no *new* ones happen.
        pushes_before_suppression = mock_push.call_count
        await pv_controller.async_evaluate()
        assert mock_push.call_count == pushes_before_suppression
        assert "Pausiert" in pv_controller.status_text

        # 05:00: price rises again -> forced charge stops, but the switch
        # and PV suppression stay off for the rest of this (low-solar) day.
        hass.states.async_set(CHEAP_PRICE_ENTITY, CHEAP_PRICE_EXPENSIVE)
        await hass.async_block_till_done()
        assert mock_release.call_count >= 1
        assert mock_set_switch.call_args.args == (False,)

        await pv_controller.async_evaluate()
        assert mock_push.call_count == pushes_before_suppression

        # Next evening: a good forecast for the day after.
        hass.states.async_set(CHEAP_FORECAST_ENTITY, "45")
        _fire_evening_eval(hass, day_offset=1)
        await hass.async_block_till_done()

        # Next midnight: rollover the other way -> switch back on, PV
        # pushes resume, and charging is not forced (no low-solar day).
        hass.states.async_set(CHEAP_PRICE_ENTITY, CHEAP_PRICE_CHEAP)
        await hass.async_block_till_done()

        assert mock_set_switch.call_args.args == (True,)
        assert mock_force_on.call_count == 1  # unchanged - not forced again

        await pv_controller.async_evaluate()
        assert mock_push.call_count >= 1


@pytest.mark.asyncio
async def test_cheap_disable_restores_immediately(hass, enable_custom_integrations):
    """Turning the feature off mid-window must hand control back right
    away, not leave the go-e stuck force-charging or the switch off with
    nothing left to ever turn it back on."""
    hass.states.async_set(ZOE_SOC_ENTITY, "50")
    hass.states.async_set(ZOE_CHARGING_ENTITY, "off")
    hass.states.async_set(ZOE_CONNECTED_ENTITY, "on")
    hass.states.async_set(PV_SOC_ENTITY, "10")
    hass.states.async_set(PV_SOLAR_ENTITY, "0")
    hass.states.async_set(PV_GRID_ENTITY, "0")
    hass.states.async_set(PV_BATTERY_ENTITY, "0")

    hass.states.async_set(CHEAP_FORECAST_ENTITY, "18")
    hass.states.async_set(CHEAP_PRICE_ENTITY, CHEAP_PRICE_EXPENSIVE)
    hass.states.async_set(CHEAP_GOE_PV_SWITCH_ENTITY, "on")

    with patch(
        "custom_components.go_e_solar_charger.goe_client.GoEClient.stop_charging",
        new=AsyncMock(),
    ), patch(
        "custom_components.go_e_solar_charger.goe_client.GoEClient.release",
        new=AsyncMock(),
    ) as mock_release, patch(
        "custom_components.go_e_solar_charger.goe_client.GoEClient.force_charging_on",
        new=AsyncMock(),
    ), patch(
        "custom_components.go_e_solar_charger.goe_client.GoEClient.push_pv_values",
        new=AsyncMock(),
    ), patch(
        "custom_components.go_e_solar_charger.cheap_controller.CheapGridChargingController._set_goe_pv_switch",
        new=AsyncMock(),
    ) as mock_set_switch:
        entry = await _make_entry(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        _fire_evening_eval(hass)
        await hass.async_block_till_done()
        hass.states.async_set(CHEAP_PRICE_ENTITY, CHEAP_PRICE_CHEAP)
        await hass.async_block_till_done()
        assert mock_set_switch.call_args.args == (False,)

        await hass.services.async_call(
            "switch",
            "turn_off",
            {"entity_id": f"switch.{DEVICE_SLUG}_guenstigstrom_aktiviert"},
            blocking=True,
        )
        await hass.async_block_till_done()

        assert mock_release.call_count >= 1
        assert mock_set_switch.call_args.args == (True,)
        assert _state(hass, f"sensor.{DEVICE_SLUG}_guenstigstrom_status") == "Deaktiviert"


@pytest.mark.asyncio
async def test_cheap_not_configured_is_inert(hass, enable_custom_integrations):
    """An entry created before this feature existed won't have the new
    cheap_* keys at all - setup must not crash, the feature just stays
    inert until the user reconfigures it."""
    hass.states.async_set(ZOE_SOC_ENTITY, "50")
    hass.states.async_set(ZOE_CHARGING_ENTITY, "off")
    hass.states.async_set(ZOE_CONNECTED_ENTITY, "off")
    hass.states.async_set(PV_SOC_ENTITY, "10")
    hass.states.async_set(PV_SOLAR_ENTITY, "0")
    hass.states.async_set(PV_GRID_ENTITY, "0")
    hass.states.async_set(PV_BATTERY_ENTITY, "0")

    with patch(
        "custom_components.go_e_solar_charger.goe_client.GoEClient.stop_charging",
        new=AsyncMock(),
    ), patch(
        "custom_components.go_e_solar_charger.goe_client.GoEClient.release",
        new=AsyncMock(),
    ), patch(
        "custom_components.go_e_solar_charger.goe_client.GoEClient.push_pv_values",
        new=AsyncMock(),
    ):
        entry = await _make_entry(
            hass,
            cheap_forecast_entity_id=None,
            cheap_price_entity_id=None,
            cheap_goe_pv_switch_entity_id=None,
        )
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert _state(hass, f"sensor.{DEVICE_SLUG}_guenstigstrom_status") == (
            'Nicht konfiguriert - bitte unter "Konfigurieren" Solar-Vorhersage, '
            "Strompreis und go-e-PV-Schalter angeben."
        )


@pytest.mark.asyncio
async def test_pv_export_override_pushes_despite_low_soc(hass, enable_custom_integrations):
    """The Powerwall itself sometimes exports a lot even while below its
    own SoC threshold (e.g. around midday in summer) - once that export
    crosses the override threshold, real values must go to go-e anyway
    instead of the zeroed safety values."""
    hass.states.async_set(ZOE_SOC_ENTITY, "50")
    hass.states.async_set(ZOE_CHARGING_ENTITY, "off")
    hass.states.async_set(ZOE_CONNECTED_ENTITY, "off")

    hass.states.async_set(PV_SOC_ENTITY, "30")  # below the 50 % threshold
    hass.states.async_set(PV_SOLAR_ENTITY, "3500")
    hass.states.async_set(PV_GRID_ENTITY, "-3500")  # exporting 3500 W > 3100 W override
    hass.states.async_set(PV_BATTERY_ENTITY, "-500")

    with patch(
        "custom_components.go_e_solar_charger.goe_client.GoEClient.stop_charging",
        new=AsyncMock(),
    ), patch(
        "custom_components.go_e_solar_charger.goe_client.GoEClient.release",
        new=AsyncMock(),
    ), patch(
        "custom_components.go_e_solar_charger.goe_client.GoEClient.push_pv_values",
        new=AsyncMock(),
    ) as mock_push, patch(
        "custom_components.go_e_solar_charger.tesla_controller.TeslaChargingController._set_tesla_switch",
        new=AsyncMock(),
    ):
        entry = await _make_entry(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert (
            hass.states.get(f"number.{DEVICE_SLUG}_pv_sofort_freigabe_ab_einspeisung")
            is not None
        )

        # below threshold, but exporting well above the override -> real
        # values sent anyway.
        assert mock_push.call_args.args[0] == {"pPv": 3500.0, "pGrid": -3500.0, "pAkku": -500.0}
        assert "trotzdem gesendet" in _state(hass, f"sensor.{DEVICE_SLUG}_pv_freigabe_status")

        # export drops back below the override, still below SoC threshold
        # -> back to the zeroed safety values.
        hass.states.async_set(PV_GRID_ENTITY, "-1000")
        await hass.async_block_till_done()
        assert mock_push.call_args.args[0] == {"pPv": 0, "pGrid": 0, "pAkku": 0}

        # raising the override threshold itself also takes effect
        # immediately, same as the other number entities.
        hass.states.async_set(PV_GRID_ENTITY, "-3500")
        await hass.async_block_till_done()
        assert mock_push.call_args.args[0] == {"pPv": 3500.0, "pGrid": -3500.0, "pAkku": -500.0}

        await hass.services.async_call(
            "number",
            "set_value",
            {
                "entity_id": f"number.{DEVICE_SLUG}_pv_sofort_freigabe_ab_einspeisung",
                "value": 4000,
            },
            blocking=True,
        )
        await hass.async_block_till_done()
        assert mock_push.call_args.args[0] == {"pPv": 0, "pGrid": 0, "pAkku": 0}


@pytest.mark.asyncio
async def test_tesla_charging_follows_powerwall_soc_and_grid_export(
    hass, enable_custom_integrations
):
    """The Tesla's own charging switch should stay off while the Powerwall
    is below its (shared, live) SoC threshold, unless enough power is
    already going into the grid to be worth using regardless - and come
    back on once the Powerwall itself reaches that threshold.

    This whole daytime PV/SoC-based gating is disabled by default for now
    (see tesla_controller.DAYTIME_GATING_ENABLED - it caused real
    start/stop cycling of the Tesla's actual charging, not just a status
    text flicker) - this test patches the flag back on to keep the
    underlying mechanism verified for whenever it's re-enabled."""
    hass.states.async_set(ZOE_SOC_ENTITY, "50")
    hass.states.async_set(ZOE_CHARGING_ENTITY, "off")
    hass.states.async_set(ZOE_CONNECTED_ENTITY, "off")

    hass.states.async_set(PV_SOC_ENTITY, "30")  # below the 50 % threshold
    hass.states.async_set(PV_SOLAR_ENTITY, "0")
    hass.states.async_set(PV_GRID_ENTITY, "-200")  # exporting, but below the 1400 W release
    hass.states.async_set(PV_BATTERY_ENTITY, "0")

    with patch(
        "custom_components.go_e_solar_charger.goe_client.GoEClient.stop_charging",
        new=AsyncMock(),
    ), patch(
        "custom_components.go_e_solar_charger.goe_client.GoEClient.release",
        new=AsyncMock(),
    ), patch(
        "custom_components.go_e_solar_charger.goe_client.GoEClient.push_pv_values",
        new=AsyncMock(),
    ), patch(
        "custom_components.go_e_solar_charger.tesla_controller.TeslaChargingController._set_tesla_switch",
        new=AsyncMock(),
    ) as mock_set_tesla, patch(
        "custom_components.go_e_solar_charger.tesla_controller.DAYTIME_GATING_ENABLED", True
    ):
        entry = await _make_entry(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert hass.states.get(f"number.{DEVICE_SLUG}_tesla_netz_freigabe") is not None
        assert (
            hass.states.get(f"switch.{DEVICE_SLUG}_tesla_ladesteuerung_aktiviert") is not None
        )
        assert hass.states.get(f"sensor.{DEVICE_SLUG}_tesla_ladesteuerung_status") is not None
        assert hass.states.get(f"button.{DEVICE_SLUG}_tesla_jetzt_pruefen") is not None

        # below SoC threshold, not enough export -> stopped.
        assert mock_set_tesla.call_args.args == (False,)
        assert "gestoppt" in _state(hass, f"sensor.{DEVICE_SLUG}_tesla_ladesteuerung_status")

        # export rises above the 1400 W release threshold -> released,
        # even though the Powerwall SoC is still below its own threshold.
        hass.states.async_set(PV_GRID_ENTITY, "-1500")
        await hass.async_block_till_done()
        assert mock_set_tesla.call_args.args == (True,)
        assert "freigegeben" in _state(hass, f"sensor.{DEVICE_SLUG}_tesla_ladesteuerung_status")

        # export drops again, but the Powerwall itself now reached the
        # threshold -> stays released via the SoC condition instead.
        calls_before = mock_set_tesla.call_count
        hass.states.async_set(PV_GRID_ENTITY, "-200")
        hass.states.async_set(PV_SOC_ENTITY, "60")
        await hass.async_block_till_done()
        assert mock_set_tesla.call_args.args == (True,)
        assert mock_set_tesla.call_count == calls_before  # no redundant call, already True

        # SoC drops back below threshold with low export -> stopped again.
        hass.states.async_set(PV_SOC_ENTITY, "30")
        await hass.async_block_till_done()
        assert mock_set_tesla.call_args.args == (False,)

        # disabling the feature while it has the Tesla stopped hands
        # control back immediately instead of leaving it stuck off.
        await hass.services.async_call(
            "switch",
            "turn_off",
            {"entity_id": f"switch.{DEVICE_SLUG}_tesla_ladesteuerung_aktiviert"},
            blocking=True,
        )
        await hass.async_block_till_done()
        assert mock_set_tesla.call_args.args == (True,)
        assert _state(hass, f"sensor.{DEVICE_SLUG}_tesla_ladesteuerung_status") == "Deaktiviert"


@pytest.mark.asyncio
async def test_tesla_reasserts_stop_periodically(hass, enable_custom_integrations):
    """The Tesla's own charging logic can decide on its own to resume
    charging while plugged in (observed in practice: it kept restarting
    for hours while the SoC-gate decision stayed "gestoppt" the whole
    time) - so a one-shot switch.turn_off isn't enough. As long as the
    decision hasn't changed, the switch call must still be repeated once
    REASSERT_INTERVAL_SECONDS has passed, but not on every single
    evaluation in between (that would spam the Tesla API).

    This whole daytime gating (reassertion included) is disabled by
    default for now: it turned out to cause real, repeated start/stop
    cycling of the actual charging session, which is worse than the
    original cosmetic-looking symptom. Patches the flag back on to keep
    this mechanism verified for whenever it's revisited."""
    hass.states.async_set(ZOE_SOC_ENTITY, "50")
    hass.states.async_set(ZOE_CHARGING_ENTITY, "off")
    hass.states.async_set(ZOE_CONNECTED_ENTITY, "off")

    hass.states.async_set(PV_SOC_ENTITY, "30")  # below the 50 % threshold
    hass.states.async_set(PV_SOLAR_ENTITY, "0")
    hass.states.async_set(PV_GRID_ENTITY, "-200")  # exporting, but below the 1400 W release
    hass.states.async_set(PV_BATTERY_ENTITY, "0")

    fake_now = [1000.0]

    with patch(
        "custom_components.go_e_solar_charger.goe_client.GoEClient.stop_charging",
        new=AsyncMock(),
    ), patch(
        "custom_components.go_e_solar_charger.goe_client.GoEClient.release",
        new=AsyncMock(),
    ), patch(
        "custom_components.go_e_solar_charger.goe_client.GoEClient.push_pv_values",
        new=AsyncMock(),
    ), patch(
        "custom_components.go_e_solar_charger.tesla_controller.TeslaChargingController._set_tesla_switch",
        new=AsyncMock(),
    ) as mock_set_tesla, patch(
        "custom_components.go_e_solar_charger.tesla_controller.time.monotonic",
        new=lambda: fake_now[0],
    ), patch(
        "custom_components.go_e_solar_charger.tesla_controller.DAYTIME_GATING_ENABLED", True
    ):
        entry = await _make_entry(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert mock_set_tesla.call_args.args == (False,)
        calls_after_setup = mock_set_tesla.call_count

        # A little later, still stopped - the decision hasn't changed and
        # the reassert interval hasn't elapsed yet, so no new call.
        fake_now[0] += 30
        hass.states.async_set(PV_GRID_ENTITY, "-210")
        await hass.async_block_till_done()
        assert mock_set_tesla.call_count == calls_after_setup

        # Past the reassert interval, still stopped - must re-apply "off"
        # again even though nothing about the decision itself changed, to
        # override the Tesla resuming charging on its own.
        fake_now[0] += 300
        hass.states.async_set(PV_GRID_ENTITY, "-220")
        await hass.async_block_till_done()
        assert mock_set_tesla.call_args.args == (False,)
        assert mock_set_tesla.call_count == calls_after_setup + 1


@pytest.mark.asyncio
async def test_tesla_daytime_gating_disabled_by_default(hass, enable_custom_integrations):
    """The daytime PV/SoC-based gating is switched off by default (see
    tesla_controller.DAYTIME_GATING_ENABLED) - the Tesla's own charging
    logic runs unmanaged during the day regardless of how wildly the
    Powerwall SoC/grid sensors swing, and the status sensor says so."""
    hass.states.async_set(ZOE_SOC_ENTITY, "50")
    hass.states.async_set(ZOE_CHARGING_ENTITY, "off")
    hass.states.async_set(ZOE_CONNECTED_ENTITY, "off")

    hass.states.async_set(PV_SOC_ENTITY, "30")  # would be well below the 50 % threshold
    hass.states.async_set(PV_SOLAR_ENTITY, "0")
    hass.states.async_set(PV_GRID_ENTITY, "-200")
    hass.states.async_set(PV_BATTERY_ENTITY, "0")

    with patch(
        "custom_components.go_e_solar_charger.goe_client.GoEClient.stop_charging",
        new=AsyncMock(),
    ), patch(
        "custom_components.go_e_solar_charger.goe_client.GoEClient.release",
        new=AsyncMock(),
    ), patch(
        "custom_components.go_e_solar_charger.goe_client.GoEClient.push_pv_values",
        new=AsyncMock(),
    ), patch(
        "custom_components.go_e_solar_charger.tesla_controller.TeslaChargingController._set_tesla_switch",
        new=AsyncMock(),
    ) as mock_set_tesla:
        entry = await _make_entry(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert mock_set_tesla.call_count == 0
        assert "vorerst deaktiviert" in _state(
            hass, f"sensor.{DEVICE_SLUG}_tesla_ladesteuerung_status"
        )

        # Even a swing that would normally flip the gating decision (SoC
        # above threshold, high export) must not touch the switch at all.
        hass.states.async_set(PV_SOC_ENTITY, "95")
        hass.states.async_set(PV_GRID_ENTITY, "-5000")
        await hass.async_block_till_done()
        assert mock_set_tesla.call_count == 0


@pytest.mark.asyncio
async def test_tesla_not_configured_is_inert(hass, enable_custom_integrations):
    """An entry created before this feature existed (or one where it was
    deliberately left blank) won't have tesla_switch_entity_id - setup
    must not crash, the feature just stays inert."""
    hass.states.async_set(ZOE_SOC_ENTITY, "50")
    hass.states.async_set(ZOE_CHARGING_ENTITY, "off")
    hass.states.async_set(ZOE_CONNECTED_ENTITY, "off")
    hass.states.async_set(PV_SOC_ENTITY, "10")
    hass.states.async_set(PV_SOLAR_ENTITY, "0")
    hass.states.async_set(PV_GRID_ENTITY, "0")
    hass.states.async_set(PV_BATTERY_ENTITY, "0")

    with patch(
        "custom_components.go_e_solar_charger.goe_client.GoEClient.stop_charging",
        new=AsyncMock(),
    ), patch(
        "custom_components.go_e_solar_charger.goe_client.GoEClient.release",
        new=AsyncMock(),
    ), patch(
        "custom_components.go_e_solar_charger.goe_client.GoEClient.push_pv_values",
        new=AsyncMock(),
    ):
        entry = await _make_entry(hass, tesla_switch_entity_id=None)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert _state(hass, f"sensor.{DEVICE_SLUG}_tesla_ladesteuerung_status") == (
            'Nicht konfiguriert - bitte unter "Konfigurieren" den Tesla-Lade-Schalter angeben.'
        )


@pytest.mark.asyncio
async def test_cheap_window_forces_both_cars_and_suppresses_tesla_own_logic(
    hass, enable_custom_integrations
):
    """On a low-solar day, once the cheap window opens, both the go-e car
    and the Tesla must be force-charged (when the Powerwall itself isn't
    charging) - and while that suppression is active, the Tesla's own PV/
    export-based gating must stay completely inert, even if its own
    sensors change in a way that would otherwise flip its decision."""
    hass.states.async_set(ZOE_SOC_ENTITY, "50")
    hass.states.async_set(ZOE_CHARGING_ENTITY, "off")
    hass.states.async_set(ZOE_CONNECTED_ENTITY, "on")
    hass.states.async_set(PV_SOC_ENTITY, "10")  # below Tesla's own threshold
    hass.states.async_set(PV_SOLAR_ENTITY, "0")
    hass.states.async_set(PV_GRID_ENTITY, "0")
    hass.states.async_set(PV_BATTERY_ENTITY, "0")  # Powerwall not charging itself

    hass.states.async_set(CHEAP_FORECAST_ENTITY, "18")  # below the 30 kWh threshold
    hass.states.async_set(CHEAP_PRICE_ENTITY, CHEAP_PRICE_EXPENSIVE)
    hass.states.async_set(CHEAP_GOE_PV_SWITCH_ENTITY, "on")

    with patch(
        "custom_components.go_e_solar_charger.goe_client.GoEClient.stop_charging",
        new=AsyncMock(),
    ), patch(
        "custom_components.go_e_solar_charger.goe_client.GoEClient.release",
        new=AsyncMock(),
    ), patch(
        "custom_components.go_e_solar_charger.goe_client.GoEClient.force_charging_on",
        new=AsyncMock(),
    ) as mock_force_on, patch(
        "custom_components.go_e_solar_charger.goe_client.GoEClient.push_pv_values",
        new=AsyncMock(),
    ), patch(
        "custom_components.go_e_solar_charger.cheap_controller.CheapGridChargingController._set_goe_pv_switch",
        new=AsyncMock(),
    ), patch(
        "custom_components.go_e_solar_charger.tesla_controller.TeslaChargingController._set_tesla_switch",
        new=AsyncMock(),
    ) as mock_set_tesla:
        entry = await _make_entry(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # A poor forecast was already available before setup - today's
        # low-solar decision is already latched right away, so Tesla's own
        # PV/export-based gating is suppressed from the start instead of
        # running normally until the price window opens.
        assert mock_set_tesla.call_args is None
        assert "Pausiert" in _state(hass, f"sensor.{DEVICE_SLUG}_tesla_ladesteuerung_status")

        _fire_evening_eval(hass)
        await hass.async_block_till_done()
        hass.states.async_set(CHEAP_PRICE_ENTITY, CHEAP_PRICE_CHEAP)
        await hass.async_block_till_done()

        assert mock_force_on.call_count == 1
        assert mock_set_tesla.call_args.args == (True,)
        assert "Auto Ladelimit" in _state(hass, f"sensor.{DEVICE_SLUG}_guenstigstrom_status")
        assert "Tesla" in _state(hass, f"sensor.{DEVICE_SLUG}_guenstigstrom_status")
        assert "Erzwungen" in _state(hass, f"sensor.{DEVICE_SLUG}_tesla_ladesteuerung_status")

        # Change a sensor that would normally flip the Tesla's own PV/
        # export-based gating (SoC now above its threshold) - while
        # suppressed, this must not touch the Tesla switch at all.
        calls_before = mock_set_tesla.call_count
        hass.states.async_set(PV_SOC_ENTITY, "90")
        await hass.async_block_till_done()
        assert mock_set_tesla.call_count == calls_before


@pytest.mark.asyncio
async def test_cheap_powerwall_arbitration_and_priority_select(hass, enable_custom_integrations):
    """While the Powerwall itself charges from the grid (above the
    configured threshold), only one of the two cars may draw power at a
    time - decided by the live-adjustable priority select entity - and
    both resume once the Powerwall stops charging itself."""
    hass.states.async_set(ZOE_SOC_ENTITY, "50")
    hass.states.async_set(ZOE_CHARGING_ENTITY, "off")
    hass.states.async_set(ZOE_CONNECTED_ENTITY, "on")
    hass.states.async_set(PV_SOC_ENTITY, "10")
    hass.states.async_set(PV_SOLAR_ENTITY, "0")
    hass.states.async_set(PV_GRID_ENTITY, "0")
    hass.states.async_set(PV_BATTERY_ENTITY, "0")  # Powerwall not charging itself yet

    hass.states.async_set(CHEAP_FORECAST_ENTITY, "18")
    hass.states.async_set(CHEAP_PRICE_ENTITY, CHEAP_PRICE_EXPENSIVE)
    hass.states.async_set(CHEAP_GOE_PV_SWITCH_ENTITY, "on")

    with patch(
        "custom_components.go_e_solar_charger.goe_client.GoEClient.stop_charging",
        new=AsyncMock(),
    ) as mock_stop, patch(
        "custom_components.go_e_solar_charger.goe_client.GoEClient.release",
        new=AsyncMock(),
    ), patch(
        "custom_components.go_e_solar_charger.goe_client.GoEClient.force_charging_on",
        new=AsyncMock(),
    ) as mock_force_on, patch(
        "custom_components.go_e_solar_charger.goe_client.GoEClient.push_pv_values",
        new=AsyncMock(),
    ), patch(
        "custom_components.go_e_solar_charger.cheap_controller.CheapGridChargingController._set_goe_pv_switch",
        new=AsyncMock(),
    ), patch(
        "custom_components.go_e_solar_charger.tesla_controller.TeslaChargingController._set_tesla_switch",
        new=AsyncMock(),
    ) as mock_set_tesla:
        entry = await _make_entry(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        priority_entity = f"select.{DEVICE_SLUG}_guenstigstrom_ladeprioritaet"
        assert hass.states.get(priority_entity) is not None
        assert _state(hass, priority_entity) == CHEAP_PRIORITY_AUTO_FIRST

        _fire_evening_eval(hass)
        await hass.async_block_till_done()
        hass.states.async_set(CHEAP_PRICE_ENTITY, CHEAP_PRICE_CHEAP)
        await hass.async_block_till_done()

        # Both charging, Powerwall not yet charging itself.
        assert mock_force_on.call_count == 1
        assert mock_set_tesla.call_args.args == (True,)

        # Powerwall starts charging itself above the 200 W default
        # threshold -> Auto Ladelimit (default priority) keeps going, the
        # Tesla gets paused.
        hass.states.async_set(PV_BATTERY_ENTITY, "-500")
        await hass.async_block_till_done()
        assert mock_set_tesla.call_args.args == (False,)
        assert mock_stop.call_count == 0  # Zoe never actually stopped
        assert "Tesla pausiert" in _state(hass, f"sensor.{DEVICE_SLUG}_guenstigstrom_status")

        # Powerwall stops charging itself again -> both resume.
        hass.states.async_set(PV_BATTERY_ENTITY, "0")
        await hass.async_block_till_done()
        assert mock_set_tesla.call_args.args == (True,)

        # Switch priority to "Tesla zuerst" live, while the Powerwall is
        # charging itself -> now Auto Ladelimit gets paused instead.
        hass.states.async_set(PV_BATTERY_ENTITY, "-500")
        await hass.async_block_till_done()
        assert mock_set_tesla.call_args.args == (False,)  # still paused (Auto has priority)

        await hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": priority_entity, "option": CHEAP_PRIORITY_TESLA_FIRST},
            blocking=True,
        )
        await hass.async_block_till_done()
        assert _state(hass, priority_entity) == CHEAP_PRIORITY_TESLA_FIRST
        assert mock_set_tesla.call_args.args == (True,)  # Tesla now has priority
        assert mock_stop.call_count == 1  # Auto Ladelimit got paused (force-off, not neutral)


@pytest.mark.asyncio
async def test_cheap_disable_hands_back_both_cars(hass, enable_custom_integrations):
    """Disabling the feature mid-window with both cars forced must hand
    control back to each car's own logic immediately - the go-e via
    release() (neutral), the Tesla via its own controller's evaluate()."""
    hass.states.async_set(ZOE_SOC_ENTITY, "50")
    hass.states.async_set(ZOE_CHARGING_ENTITY, "off")
    hass.states.async_set(ZOE_CONNECTED_ENTITY, "on")
    hass.states.async_set(PV_SOC_ENTITY, "10")
    hass.states.async_set(PV_SOLAR_ENTITY, "0")
    hass.states.async_set(PV_GRID_ENTITY, "0")
    hass.states.async_set(PV_BATTERY_ENTITY, "0")

    hass.states.async_set(CHEAP_FORECAST_ENTITY, "18")
    hass.states.async_set(CHEAP_PRICE_ENTITY, CHEAP_PRICE_EXPENSIVE)
    hass.states.async_set(CHEAP_GOE_PV_SWITCH_ENTITY, "on")

    with patch(
        "custom_components.go_e_solar_charger.goe_client.GoEClient.stop_charging",
        new=AsyncMock(),
    ), patch(
        "custom_components.go_e_solar_charger.goe_client.GoEClient.release",
        new=AsyncMock(),
    ) as mock_release, patch(
        "custom_components.go_e_solar_charger.goe_client.GoEClient.force_charging_on",
        new=AsyncMock(),
    ), patch(
        "custom_components.go_e_solar_charger.goe_client.GoEClient.push_pv_values",
        new=AsyncMock(),
    ), patch(
        "custom_components.go_e_solar_charger.cheap_controller.CheapGridChargingController._set_goe_pv_switch",
        new=AsyncMock(),
    ) as mock_set_switch, patch(
        "custom_components.go_e_solar_charger.tesla_controller.TeslaChargingController._set_tesla_switch",
        new=AsyncMock(),
    ) as mock_set_tesla:
        entry = await _make_entry(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        _fire_evening_eval(hass)
        await hass.async_block_till_done()
        hass.states.async_set(CHEAP_PRICE_ENTITY, CHEAP_PRICE_CHEAP)
        await hass.async_block_till_done()

        assert mock_set_tesla.call_args.args == (True,)

        await hass.services.async_call(
            "switch",
            "turn_off",
            {"entity_id": f"switch.{DEVICE_SLUG}_guenstigstrom_aktiviert"},
            blocking=True,
        )
        await hass.async_block_till_done()

        assert mock_release.call_count >= 1  # go-e handed back (neutral), not force-off
        assert mock_set_switch.call_args.args == (True,)  # go-e's own PV switch back on
        # Tesla's daytime PV/SoC gating is disabled for now (see
        # tesla_controller.DAYTIME_GATING_ENABLED), so re-evaluating it is
        # a no-op - the switch is simply left as the cheap window last
        # forced it (still charging) rather than being taken over again.
        assert mock_set_tesla.call_args.args == (True,)
        assert _state(hass, f"sensor.{DEVICE_SLUG}_guenstigstrom_status") == "Deaktiviert"


@pytest.mark.asyncio
async def test_custom_car_names_appear_everywhere(hass, enable_custom_integrations):
    """Both cars can be freely renamed (zoe_car_name/tesla_car_name) - the
    custom names must show up in their own entities' friendly names, in
    the priority select's options, and in the cheap-grid-charging status
    text, instead of the hardcoded "Auto Ladelimit"/"Tesla" defaults."""
    hass.states.async_set(ZOE_SOC_ENTITY, "50")
    hass.states.async_set(ZOE_CHARGING_ENTITY, "off")
    hass.states.async_set(ZOE_CONNECTED_ENTITY, "on")
    hass.states.async_set(PV_SOC_ENTITY, "10")
    hass.states.async_set(PV_SOLAR_ENTITY, "0")
    hass.states.async_set(PV_GRID_ENTITY, "0")
    hass.states.async_set(PV_BATTERY_ENTITY, "0")  # Powerwall not charging itself

    hass.states.async_set(CHEAP_FORECAST_ENTITY, "18")
    hass.states.async_set(CHEAP_PRICE_ENTITY, CHEAP_PRICE_EXPENSIVE)
    hass.states.async_set(CHEAP_GOE_PV_SWITCH_ENTITY, "on")

    with patch(
        "custom_components.go_e_solar_charger.goe_client.GoEClient.stop_charging",
        new=AsyncMock(),
    ), patch(
        "custom_components.go_e_solar_charger.goe_client.GoEClient.release",
        new=AsyncMock(),
    ), patch(
        "custom_components.go_e_solar_charger.goe_client.GoEClient.force_charging_on",
        new=AsyncMock(),
    ), patch(
        "custom_components.go_e_solar_charger.goe_client.GoEClient.push_pv_values",
        new=AsyncMock(),
    ), patch(
        "custom_components.go_e_solar_charger.cheap_controller.CheapGridChargingController._set_goe_pv_switch",
        new=AsyncMock(),
    ), patch(
        "custom_components.go_e_solar_charger.tesla_controller.TeslaChargingController._set_tesla_switch",
        new=AsyncMock(),
    ):
        entry = await _make_entry(hass, zoe_car_name="Zoe", tesla_car_name="Model 3")
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # Zoe's own entities carry the custom name.
        assert hass.states.get(f"number.{DEVICE_SLUG}_zoe_ladelimit") is not None
        assert hass.states.get(f"switch.{DEVICE_SLUG}_zoe_ladelimit_aktiviert") is not None
        assert hass.states.get(f"sensor.{DEVICE_SLUG}_zoe_ladelimit_status") is not None

        # Tesla's (here "Model 3") own entities carry the custom name too.
        assert hass.states.get(f"number.{DEVICE_SLUG}_model_3_netz_freigabe") is not None
        assert (
            hass.states.get(f"switch.{DEVICE_SLUG}_model_3_ladesteuerung_aktiviert") is not None
        )
        assert hass.states.get(f"sensor.{DEVICE_SLUG}_model_3_ladesteuerung_status") is not None
        assert hass.states.get(f"button.{DEVICE_SLUG}_model_3_jetzt_pruefen") is not None

        # The priority select's options/default reflect the custom names.
        priority_entity = f"select.{DEVICE_SLUG}_guenstigstrom_ladeprioritaet"
        priority_state = hass.states.get(priority_entity)
        assert priority_state is not None
        assert priority_state.attributes["options"] == ["Zoe zuerst", "Model 3 zuerst"]
        assert priority_state.state == "Zoe zuerst"

        # The cheap-grid-charging status text uses the custom names too.
        _fire_evening_eval(hass)
        await hass.async_block_till_done()
        hass.states.async_set(CHEAP_PRICE_ENTITY, CHEAP_PRICE_CHEAP)
        await hass.async_block_till_done()

        status = _state(hass, f"sensor.{DEVICE_SLUG}_guenstigstrom_status")
        assert "Zoe Ladelimit" in status
        assert "Model 3" in status
