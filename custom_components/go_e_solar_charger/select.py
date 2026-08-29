"""Live-adjustable priority between the two cars for the cheap-grid-
charging feature, when the Powerwall itself is charging from the grid and
only one of them may draw power at a time. Deliberately not a config_flow
field - see cheap_controller.py/cheap_logic.py for the arbitration logic
this feeds into."""
from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import CHEAP_PRIORITY_AUTO_FIRST, CHEAP_PRIORITY_TESLA_FIRST, DOMAIN
from .entity import device_info


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    controllers = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([CheapCarPrioritySelect(controllers["cheap"], entry)])


class CheapCarPrioritySelect(SelectEntity, RestoreEntity):
    _attr_has_entity_name = True
    _attr_name = "Guenstigstrom Ladeprioritaet"
    _attr_icon = "mdi:sort-numeric-variant"
    _attr_options = [CHEAP_PRIORITY_AUTO_FIRST, CHEAP_PRIORITY_TESLA_FIRST]

    def __init__(self, controller, entry: ConfigEntry) -> None:
        self._controller = controller
        self._attr_unique_id = f"{entry.entry_id}_cheap_car_priority"
        self._attr_current_option = controller.car_priority
        self._attr_device_info = device_info(entry)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state in self._attr_options:
            self._attr_current_option = last_state.state
            self._controller.car_priority = last_state.state

    async def async_select_option(self, option: str) -> None:
        self._attr_current_option = option
        self.async_write_ha_state()
        await self._controller.async_set_car_priority(option)
