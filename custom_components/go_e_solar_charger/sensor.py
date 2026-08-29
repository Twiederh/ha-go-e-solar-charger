from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
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
            ZoeStatusSensor(controllers["zoe"], entry),
            PvStatusSensor(controllers["pv"], entry),
        ]
    )


class ZoeStatusSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Auto Ladelimit Status"
    _attr_icon = "mdi:ev-station"
    _attr_should_poll = False

    def __init__(self, controller: ZoeChargeLimitController, entry: ConfigEntry) -> None:
        self._controller = controller
        self._attr_unique_id = f"{entry.entry_id}_zoe_status"
        self._attr_device_info = device_info(entry)

    @property
    def native_value(self) -> str:
        return self._controller.status_text

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, self._controller.signal, self._handle_update)
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()


class PvStatusSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_name = "PV-Freigabe Status"
    _attr_icon = "mdi:solar-power-variant"
    _attr_should_poll = False

    def __init__(self, controller: PvSurplusController, entry: ConfigEntry) -> None:
        self._controller = controller
        self._attr_unique_id = f"{entry.entry_id}_pv_status"
        self._attr_device_info = device_info(entry)

    @property
    def native_value(self) -> str:
        return self._controller.status_text

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, self._controller.signal, self._handle_update)
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()
