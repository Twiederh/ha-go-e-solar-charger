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
    }
    data.update(overrides)
    entry = MockConfigEntry(domain=DOMAIN, data=data, title="go-e Solar Charger")
    entry.add_to_hass(hass)
    return entry


def _state(hass, entity_id):
    s = hass.states.get(entity_id)
    return s.state if s else None


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
