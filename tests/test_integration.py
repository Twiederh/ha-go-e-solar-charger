"""End-to-end test of the integration against a real (test-mode) Home
Assistant core: sets up the config entry, lets the number/switch/sensor/
button entities register, then drives the Zoe SoC sensor across the limit
and checks that the integration calls a mock go-e server correctly and
updates its status sensor.

Not part of the shipped custom_component - a development-time check.
"""
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zoe_charge_limit.const import DOMAIN

SOC_ENTITY = "sensor.zoe_batterie_soc"
CHARGING_ENTITY = "binary_sensor.goe_charging"
CONNECTED_ENTITY = "binary_sensor.goe_car_connected"


async def _make_entry(hass, **overrides):
    data = {
        "soc_entity_id": SOC_ENTITY,
        "charging_entity_id": CHARGING_ENTITY,
        "charging_on_state": "on",
        "car_connected_entity_id": CONNECTED_ENTITY,
        "car_connected_on_state": "on",
        "goe_host": "127.0.0.1:1",  # unused when go-e calls are mocked
        "goe_api_key": "",
        "default_limit": 80,
    }
    data.update(overrides)
    entry = MockConfigEntry(domain=DOMAIN, data=data, title="Zoe Ladelimit")
    entry.add_to_hass(hass)
    return entry


def _status(hass):
    state = hass.states.get(f"sensor.zoe_ladelimit_status")
    return state.state if state else None


@pytest.mark.asyncio
async def test_full_flow_stops_and_releases(hass, enable_custom_integrations):
    hass.states.async_set(SOC_ENTITY, "50")
    hass.states.async_set(CHARGING_ENTITY, "on")
    hass.states.async_set(CONNECTED_ENTITY, "on")

    with patch(
        "custom_components.zoe_charge_limit.goe_client.GoEClient.stop_charging",
        new=AsyncMock(),
    ) as mock_stop, patch(
        "custom_components.zoe_charge_limit.goe_client.GoEClient.release",
        new=AsyncMock(),
    ) as mock_release:
        entry = await _make_entry(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # entities exist
        assert hass.states.get("number.zoe_ladelimit_ladelimit") is not None
        assert hass.states.get("switch.zoe_ladelimit_aktiviert") is not None
        assert hass.states.get("sensor.zoe_ladelimit_status") is not None
        assert hass.states.get("button.zoe_ladelimit_jetzt_stoppen") is not None

        assert mock_stop.call_count == 0
        assert "Laedt" in _status(hass)

        # SoC climbs to the limit -> must stop exactly once
        hass.states.async_set(SOC_ENTITY, "80")
        await hass.async_block_till_done()
        assert mock_stop.call_count == 1
        assert "gestoppt" in _status(hass)

        # staying above the limit must not call stop again
        hass.states.async_set(SOC_ENTITY, "81")
        await hass.async_block_till_done()
        assert mock_stop.call_count == 1

        # raising the limit above current SoC releases the charger again
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": "number.zoe_ladelimit_ladelimit", "value": 90},
            blocking=True,
        )
        await hass.async_block_till_done()
        assert mock_release.call_count == 1
        assert "Laedt" in _status(hass)

        # car disconnects -> nothing left to release (already released), no crash
        hass.states.async_set(CONNECTED_ENTITY, "off")
        hass.states.async_set(CHARGING_ENTITY, "off")
        await hass.async_block_till_done()
        assert _status(hass) == "Kein Fahrzeug verbunden"

        # push it back over the limit and disable the switch instead of
        # waiting for a disconnect - must release too
        hass.states.async_set(CONNECTED_ENTITY, "on")
        hass.states.async_set(CHARGING_ENTITY, "on")
        hass.states.async_set(SOC_ENTITY, "95")
        await hass.async_block_till_done()
        assert mock_stop.call_count == 2

        await hass.services.async_call(
            "switch",
            "turn_off",
            {"entity_id": "switch.zoe_ladelimit_aktiviert"},
            blocking=True,
        )
        await hass.async_block_till_done()
        assert mock_release.call_count == 2
        assert _status(hass) == "Deaktiviert"

        # re-enabling while the car is still sitting above the limit (go-e
        # resumed normal charging while we were disabled) must immediately
        # re-enforce the stop - this is a safety property, not a side effect
        await hass.services.async_call(
            "switch",
            "turn_on",
            {"entity_id": "switch.zoe_ladelimit_aktiviert"},
            blocking=True,
        )
        await hass.async_block_till_done()
        assert mock_stop.call_count == 3
        assert "gestoppt" in _status(hass)

        # SoC then drops back below the limit -> released again
        hass.states.async_set(SOC_ENTITY, "10")
        await hass.async_block_till_done()
        assert mock_release.call_count == 3
        assert "Laedt" in _status(hass)

        # manual "stop now" button works independent of SoC
        await hass.services.async_call(
            "button",
            "press",
            {"entity_id": "button.zoe_ladelimit_jetzt_stoppen"},
            blocking=True,
        )
        await hass.async_block_till_done()
        assert mock_stop.call_count == 4
        assert _status(hass) == "Manuell gestoppt"


@pytest.mark.asyncio
async def test_restores_limit_and_enabled_after_reload(hass, enable_custom_integrations):
    hass.states.async_set(SOC_ENTITY, "50")
    hass.states.async_set(CHARGING_ENTITY, "on")
    hass.states.async_set(CONNECTED_ENTITY, "on")

    with patch(
        "custom_components.zoe_charge_limit.goe_client.GoEClient.stop_charging",
        new=AsyncMock(),
    ), patch(
        "custom_components.zoe_charge_limit.goe_client.GoEClient.release",
        new=AsyncMock(),
    ):
        entry = await _make_entry(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": "number.zoe_ladelimit_ladelimit", "value": 65},
            blocking=True,
        )
        await hass.services.async_call(
            "switch",
            "turn_off",
            {"entity_id": "switch.zoe_ladelimit_aktiviert"},
            blocking=True,
        )
        await hass.async_block_till_done()

        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert hass.states.get("number.zoe_ladelimit_ladelimit").state == "65.0" \
            or hass.states.get("number.zoe_ladelimit_ladelimit").state == "65"
        assert hass.states.get("switch.zoe_ladelimit_aktiviert").state == "off"
        assert _status(hass) == "Deaktiviert"
