from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import device_info
from .pv_controller import PvSurplusController
from .zoe_controller import ZoeChargeLimitController


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    controllers = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            ZoeStopNowButton(controllers["zoe"], entry),
            PvPushNowButton(controllers["pv"], entry),
        ]
    )


class ZoeStopNowButton(ButtonEntity):
    """Manual override, mainly useful to verify the go-e connection works
    without having to wait for the real SoC limit to be reached."""

    _attr_has_entity_name = True
    _attr_name = "Laden jetzt stoppen"
    _attr_icon = "mdi:stop-circle-outline"

    def __init__(self, controller: ZoeChargeLimitController, entry: ConfigEntry) -> None:
        self._controller = controller
        self._attr_unique_id = f"{entry.entry_id}_zoe_stop_now"
        self._attr_device_info = device_info(entry)

    async def async_press(self) -> None:
        await self._controller.async_manual_stop()


class PvPushNowButton(ButtonEntity):
    """Pushes the currently computed pPv/pGrid/pAkku values (or the zeroed
    safety values, if below the threshold) immediately - useful to verify
    the go-e connection without waiting for the next sensor change or
    keep-alive tick."""

    _attr_has_entity_name = True
    _attr_name = "PV Jetzt senden"
    _attr_icon = "mdi:refresh"

    def __init__(self, controller: PvSurplusController, entry: ConfigEntry) -> None:
        self._controller = controller
        self._attr_unique_id = f"{entry.entry_id}_pv_push_now"
        self._attr_device_info = device_info(entry)

    async def async_press(self) -> None:
        await self._controller.async_manual_push()
