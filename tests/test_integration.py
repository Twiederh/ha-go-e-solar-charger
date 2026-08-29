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
        "cheap_forecast_entity_id": CHEAP_FORECAST_ENTITY,
        "cheap_price_entity_id": CHEAP_PRICE_ENTITY,
        "cheap_goe_pv_switch_entity_id": CHEAP_GOE_PV_SWITCH_ENTITY,
        "cheap_forecast_threshold_kwh": 30,
        "cheap_price_threshold_ct": 20,
    }
    data.update(overrides)
    entry = MockConfigEntry(domain=DOMAIN, data=data, title="go-e Solar Charger")
    entry.add_to_hass(hass)
    return entry


def _state(hass, entity_id):
    s = hass.states.get(entity_id)
    return s.state if s else None


def _fire_evening_eval(hass, day_offset=0):
    """`async_track_time_change` schedules its next occurrence 24h after
    the last one actually fired (real event-loop time), so firing the same
    day's target a second time in one test never fires again - each
    subsequent call in a test needs its own day_offset to land on the
    occurrence the tracker is actually now waiting for."""
    target = dt_util.now().replace(
        hour=CHEAP_FORECAST_EVAL_HOUR, minute=CHEAP_FORECAST_EVAL_MINUTE, second=0, microsecond=0
    ) + timedelta(days=day_offset)
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

        # Evening: latch tomorrow's decision. Not applied to "today" yet.
        _fire_evening_eval(hass)
        await hass.async_block_till_done()
        assert "Normaler Tag" in _state(hass, f"sensor.{DEVICE_SLUG}_guenstigstrom_status")

        # Midnight: price drops -> daily rollover + forced charge starts.
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
