from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .controller import ChargeLimitController
from .entity import device_info


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    controller: ChargeLimitController = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ZoeChargeLimitStopButton(controller, entry)])


class ZoeChargeLimitStopButton(ButtonEntity):
    """Manual override, mainly useful to verify the go-e connection works
    without having to wait for the real SoC limit to be reached."""

    _attr_has_entity_name = True
    _attr_name = "Jetzt stoppen"
    _attr_icon = "mdi:stop-circle-outline"

    def __init__(self, controller: ChargeLimitController, entry: ConfigEntry) -> None:
        self._controller = controller
        self._attr_unique_id = f"{entry.entry_id}_stop_now"
        self._attr_device_info = device_info(entry)

    async def async_press(self) -> None:
        await self._controller.async_manual_stop()
