"""go-e Solar Charger (Home Assistant edition).

Two independent features, one go-e Charger, one device in the HA UI:

- Zoe charge limit: stops the go-e once the Renault Zoe reaches a
  configurable SoC, using sensors already provided by other integrations
  (no direct Powerwall/go-e polling for status here) - see
  zoe_controller.py / zoe_logic.py. Talks to go-e via "frc" (force state).
- PV-surplus push: feeds pPv/pGrid/pAkku into go-e's own PV-surplus
  charging logic once the Powerwall's SoC is above a configurable
  threshold - see pv_controller.py / pv_logic.py. Talks to go-e via "ids".

These two go-e API mechanisms are independent of each other, so both
features can run at once without interfering.
"""
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, PLATFORMS
from .pv_controller import PvSurplusController
from .zoe_controller import ZoeChargeLimitController


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})
    zoe_controller = ZoeChargeLimitController(hass, entry)
    pv_controller = PvSurplusController(hass, entry)
    hass.data[DOMAIN][entry.entry_id] = {
        "zoe": zoe_controller,
        "pv": pv_controller,
    }

    # Entities restore their own state (limit/threshold/enabled) during this
    # call and push it into the controllers - so it must happen before
    # either controller's first evaluation.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    await zoe_controller.async_setup()
    await pv_controller.async_setup()
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        controllers = hass.data[DOMAIN].pop(entry.entry_id)
        controllers["zoe"].async_unload()
        controllers["pv"].async_unload()
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
