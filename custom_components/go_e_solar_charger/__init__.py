"""go-e Solar Charger (Home Assistant edition).

Four independent features, one go-e Charger, one device in the HA UI:

- Auto charge limit: stops the go-e once the car reaches a configurable
  SoC, using sensors already provided by other integrations (no direct
  Powerwall/go-e polling for status here) - see zoe_controller.py /
  zoe_logic.py. Not tied to any particular car brand - any EV exposing a
  SoC sensor (and ideally a charging/connected sensor) works. Internal
  module/class names still say "zoe" (this started as a Renault Zoe
  project) but nothing in the logic is Zoe-specific. Talks to go-e via
  "frc" (force state).
- PV-surplus push: feeds pPv/pGrid/pAkku into go-e's own PV-surplus
  charging logic once the Powerwall's SoC is above a configurable
  threshold (or, below that threshold, once the Powerwall's own export
  exceeds a separate override threshold) - see pv_controller.py /
  pv_logic.py. Talks to go-e via "ids".
- Cheap-grid charging: on days with a poor solar forecast, suppresses the
  PV-surplus feature entirely and force-charges from the grid instead
  during a cheap price window - see cheap_controller.py / cheap_logic.py.
  Also toggles go-e's own PV-surplus switch (a separate entity from
  another integration) off for the day and back on afterwards, and defers
  to the Auto charge limit's SoC-based stop at all times (frc changes
  triggered by that feature always win, since they react to real SoC
  changes rather than a fixed schedule).
- Tesla charge gating: starts/stops a second car's own (independent)
  charging solution via a plain switch, gated by the same Powerwall SoC/
  grid sensors and *live* threshold as the PV-surplus feature above, with
  its own lower grid-export override - see tesla_controller.py /
  tesla_logic.py. Does not talk to go-e at all.

Configured for an entry created before the cheap-grid or Tesla features
existed, those controllers simply stay inert ("not configured") until the
entry is reconfigured with their new fields - they never crash setup for
an older entry missing them.
"""
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .cheap_controller import CheapGridChargingController
from .const import DOMAIN, PLATFORMS
from .pv_controller import PvSurplusController
from .tesla_controller import TeslaChargingController
from .zoe_controller import ZoeChargeLimitController


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})
    zoe_controller = ZoeChargeLimitController(hass, entry)
    pv_controller = PvSurplusController(hass, entry)
    tesla_controller = TeslaChargingController(hass, entry, pv_controller)
    cheap_controller = CheapGridChargingController(
        hass,
        entry,
        on_frc_changed=zoe_controller.async_evaluate,
        tesla_controller=tesla_controller,
    )
    pv_controller.set_suppressor(cheap_controller)
    tesla_controller.set_suppressor(cheap_controller)
    hass.data[DOMAIN][entry.entry_id] = {
        "zoe": zoe_controller,
        "pv": pv_controller,
        "cheap": cheap_controller,
        "tesla": tesla_controller,
    }

    # Entities restore their own state (limit/threshold/enabled) during this
    # call and push it into the controllers - so it must happen before any
    # controller's first evaluation.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    await zoe_controller.async_setup()
    await pv_controller.async_setup()
    await cheap_controller.async_setup()
    await tesla_controller.async_setup()
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        controllers = hass.data[DOMAIN].pop(entry.entry_id)
        controllers["zoe"].async_unload()
        controllers["pv"].async_unload()
        controllers["cheap"].async_unload()
        controllers["tesla"].async_unload()
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
