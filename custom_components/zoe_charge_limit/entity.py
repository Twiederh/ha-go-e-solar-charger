"""Shared helper so all four entities of one config entry group under a
single device in the Home Assistant UI."""
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN


def device_info(entry: ConfigEntry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title or "Zoe Ladelimit",
        manufacturer="Eigenbau",
        model="Zoe Ladelimit (go-e)",
    )
