from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN, MAX_LIMIT, MIN_LIMIT
from .controller import ChargeLimitController
from .entity import device_info


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    controller: ChargeLimitController = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ZoeChargeLimitNumber(controller, entry)])


class ZoeChargeLimitNumber(NumberEntity, RestoreEntity):
    _attr_has_entity_name = True
    _attr_name = "Ladelimit"
    _attr_native_min_value = MIN_LIMIT
    _attr_native_max_value = MAX_LIMIT
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "%"
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:battery-charging-80"

    def __init__(self, controller: ChargeLimitController, entry: ConfigEntry) -> None:
        self._controller = controller
        self._attr_unique_id = f"{entry.entry_id}_limit"
        self._attr_native_value = controller.limit
        self._attr_device_info = device_info(entry)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state not in ("unknown", "unavailable"):
            try:
                value = float(last_state.state)
            except ValueError:
                value = None
            if value is not None:
                self._attr_native_value = value
                self._controller.limit = value

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self.async_write_ha_state()
        await self._controller.async_set_limit(value)
