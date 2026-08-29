from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .cheap_controller import CheapGridChargingController
from .const import DOMAIN
from .entity import device_info
from .pv_controller import PvSurplusController
from .tesla_controller import TeslaChargingController
from .zoe_controller import ZoeChargeLimitController


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    controllers = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            ZoeLimitEnableSwitch(controllers["zoe"], entry),
            PvPushEnableSwitch(controllers["pv"], entry),
            CheapEnableSwitch(controllers["cheap"], entry),
            TeslaEnableSwitch(controllers["tesla"], entry),
        ]
    )


class ZoeLimitEnableSwitch(SwitchEntity, RestoreEntity):
    _attr_has_entity_name = True
    _attr_name = "Auto Ladelimit aktiviert"
    _attr_icon = "mdi:power"

    def __init__(self, controller: ZoeChargeLimitController, entry: ConfigEntry) -> None:
        self._controller = controller
        self._attr_unique_id = f"{entry.entry_id}_zoe_enabled"
        self._attr_is_on = True
        self._attr_device_info = device_info(entry)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state in ("on", "off"):
            value = last_state.state == "on"
            self._attr_is_on = value
            self._controller.enabled = value

    async def async_turn_on(self, **kwargs) -> None:
        self._attr_is_on = True
        self.async_write_ha_state()
        await self._controller.async_set_enabled(True)

    async def async_turn_off(self, **kwargs) -> None:
        self._attr_is_on = False
        self.async_write_ha_state()
        await self._controller.async_set_enabled(False)


class TeslaEnableSwitch(SwitchEntity, RestoreEntity):
    _attr_has_entity_name = True
    _attr_name = "Tesla Ladesteuerung aktiviert"
    _attr_icon = "mdi:power"

    def __init__(self, controller: TeslaChargingController, entry: ConfigEntry) -> None:
        self._controller = controller
        self._attr_unique_id = f"{entry.entry_id}_tesla_enabled"
        self._attr_is_on = True
        self._attr_device_info = device_info(entry)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state in ("on", "off"):
            value = last_state.state == "on"
            self._attr_is_on = value
            self._controller.enabled = value

    async def async_turn_on(self, **kwargs) -> None:
        self._attr_is_on = True
        self.async_write_ha_state()
        await self._controller.async_set_enabled(True)

    async def async_turn_off(self, **kwargs) -> None:
        self._attr_is_on = False
        self.async_write_ha_state()
        await self._controller.async_set_enabled(False)


class PvPushEnableSwitch(SwitchEntity, RestoreEntity):
    _attr_has_entity_name = True
    _attr_name = "PV-Freigabe aktiviert"
    _attr_icon = "mdi:power"

    def __init__(self, controller: PvSurplusController, entry: ConfigEntry) -> None:
        self._controller = controller
        self._attr_unique_id = f"{entry.entry_id}_pv_enabled"
        self._attr_is_on = True
        self._attr_device_info = device_info(entry)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state in ("on", "off"):
            value = last_state.state == "on"
            self._attr_is_on = value
            self._controller.enabled = value

    async def async_turn_on(self, **kwargs) -> None:
        self._attr_is_on = True
        self.async_write_ha_state()
        await self._controller.async_set_enabled(True)

    async def async_turn_off(self, **kwargs) -> None:
        self._attr_is_on = False
        self.async_write_ha_state()
        await self._controller.async_set_enabled(False)


class CheapEnableSwitch(SwitchEntity, RestoreEntity):
    _attr_has_entity_name = True
    _attr_name = "Guenstigstrom aktiviert"
    _attr_icon = "mdi:power"

    def __init__(self, controller: CheapGridChargingController, entry: ConfigEntry) -> None:
        self._controller = controller
        self._attr_unique_id = f"{entry.entry_id}_cheap_enabled"
        self._attr_is_on = True
        self._attr_device_info = device_info(entry)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state in ("on", "off"):
            value = last_state.state == "on"
            self._attr_is_on = value
            self._controller.enabled = value

    async def async_turn_on(self, **kwargs) -> None:
        self._attr_is_on = True
        self.async_write_ha_state()
        await self._controller.async_set_enabled(True)

    async def async_turn_off(self, **kwargs) -> None:
        self._attr_is_on = False
        self.async_write_ha_state()
        await self._controller.async_set_enabled(False)
