"""Zoe Ladelimit (go-e) - stops the go-e Charger once the Renault Zoe's
battery reaches a configurable state of charge, using sensors already
provided by other Home Assistant integrations (no direct polling of the
Powerwall or go-e is done for status - only the stop/release commands go
straight to the go-e's local API)."""
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, PLATFORMS
from .controller import ChargeLimitController


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})
    controller = ChargeLimitController(hass, entry)
    hass.data[DOMAIN][entry.entry_id] = controller

    # Entities restore their own state (limit/enabled) during this call and
    # push it into the controller - so it must happen before the
    # controller's first evaluation.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    await controller.async_setup()
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        controller: ChargeLimitController = hass.data[DOMAIN].pop(entry.entry_id)
        controller.async_unload()
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
