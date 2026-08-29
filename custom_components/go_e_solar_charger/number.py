from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .cheap_controller import CheapGridChargingController
from .const import (
    DOMAIN,
    MAX_CHEAP_FORECAST_THRESHOLD,
    MAX_CHEAP_POWERWALL_CHARGE_THRESHOLD,
    MAX_CHEAP_PRICE_THRESHOLD,
    MAX_PV_EXPORT_OVERRIDE_THRESHOLD,
    MAX_PV_THRESHOLD,
    MAX_TESLA_GRID_RELEASE_THRESHOLD,
    MAX_ZOE_LIMIT,
    MIN_CHEAP_FORECAST_THRESHOLD,
    MIN_CHEAP_POWERWALL_CHARGE_THRESHOLD,
    MIN_CHEAP_PRICE_THRESHOLD,
    MIN_PV_EXPORT_OVERRIDE_THRESHOLD,
    MIN_PV_THRESHOLD,
    MIN_TESLA_GRID_RELEASE_THRESHOLD,
    MIN_ZOE_LIMIT,
)
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
            ZoeLimitNumber(controllers["zoe"], entry),
            PvThresholdNumber(controllers["pv"], entry),
            PvExportOverrideNumber(controllers["pv"], entry),
            CheapForecastThresholdNumber(controllers["cheap"], entry),
            CheapPriceThresholdNumber(controllers["cheap"], entry),
            CheapPowerwallChargeThresholdNumber(controllers["cheap"], entry),
            TeslaGridReleaseThresholdNumber(controllers["tesla"], entry),
        ]
    )


class ZoeLimitNumber(NumberEntity, RestoreEntity):
    _attr_has_entity_name = True
    _attr_name = "Auto Ladelimit"
    _attr_native_min_value = MIN_ZOE_LIMIT
    _attr_native_max_value = MAX_ZOE_LIMIT
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "%"
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:battery-charging-80"

    def __init__(self, controller: ZoeChargeLimitController, entry: ConfigEntry) -> None:
        self._controller = controller
        self._attr_unique_id = f"{entry.entry_id}_zoe_limit"
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


class PvThresholdNumber(NumberEntity, RestoreEntity):
    _attr_has_entity_name = True
    _attr_name = "PV-Freigabe ab Akkustand"
    _attr_native_min_value = MIN_PV_THRESHOLD
    _attr_native_max_value = MAX_PV_THRESHOLD
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "%"
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:battery-sync"

    def __init__(self, controller: PvSurplusController, entry: ConfigEntry) -> None:
        self._controller = controller
        self._attr_unique_id = f"{entry.entry_id}_pv_threshold"
        self._attr_native_value = controller.threshold
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
                self._controller.threshold = value

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self.async_write_ha_state()
        await self._controller.async_set_threshold(value)


class PvExportOverrideNumber(NumberEntity, RestoreEntity):
    _attr_has_entity_name = True
    _attr_name = "PV Sofort-Freigabe ab Einspeisung"
    _attr_native_min_value = MIN_PV_EXPORT_OVERRIDE_THRESHOLD
    _attr_native_max_value = MAX_PV_EXPORT_OVERRIDE_THRESHOLD
    _attr_native_step = 50
    _attr_native_unit_of_measurement = "W"
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:transmission-tower-export"

    def __init__(self, controller: PvSurplusController, entry: ConfigEntry) -> None:
        self._controller = controller
        self._attr_unique_id = f"{entry.entry_id}_pv_export_override_threshold"
        self._attr_native_value = controller.export_override_w
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
                self._controller.export_override_w = value

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self.async_write_ha_state()
        await self._controller.async_set_export_override(value)


class CheapForecastThresholdNumber(NumberEntity, RestoreEntity):
    _attr_has_entity_name = True
    _attr_name = "Guenstigstrom Solar-Schwelle"
    _attr_native_min_value = MIN_CHEAP_FORECAST_THRESHOLD
    _attr_native_max_value = MAX_CHEAP_FORECAST_THRESHOLD
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "kWh"
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:weather-cloudy-clock"

    def __init__(self, controller: CheapGridChargingController, entry: ConfigEntry) -> None:
        self._controller = controller
        self._attr_unique_id = f"{entry.entry_id}_cheap_forecast_threshold"
        self._attr_native_value = controller.forecast_threshold
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
                self._controller.forecast_threshold = value

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self.async_write_ha_state()
        await self._controller.async_set_forecast_threshold(value)


class CheapPriceThresholdNumber(NumberEntity, RestoreEntity):
    _attr_has_entity_name = True
    _attr_name = "Guenstigstrom Preis-Schwelle"
    _attr_native_min_value = MIN_CHEAP_PRICE_THRESHOLD
    _attr_native_max_value = MAX_CHEAP_PRICE_THRESHOLD
    _attr_native_step = 0.5
    _attr_native_unit_of_measurement = "ct"
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:currency-eur"

    def __init__(self, controller: CheapGridChargingController, entry: ConfigEntry) -> None:
        self._controller = controller
        self._attr_unique_id = f"{entry.entry_id}_cheap_price_threshold"
        self._attr_native_value = controller.price_threshold
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
                self._controller.price_threshold = value

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self.async_write_ha_state()
        await self._controller.async_set_price_threshold(value)


class CheapPowerwallChargeThresholdNumber(NumberEntity, RestoreEntity):
    _attr_has_entity_name = True
    _attr_name = "Guenstigstrom Powerwall-Ladeleistung-Schwelle"
    _attr_native_min_value = MIN_CHEAP_POWERWALL_CHARGE_THRESHOLD
    _attr_native_max_value = MAX_CHEAP_POWERWALL_CHARGE_THRESHOLD
    _attr_native_step = 50
    _attr_native_unit_of_measurement = "W"
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:home-battery"

    def __init__(self, controller: CheapGridChargingController, entry: ConfigEntry) -> None:
        self._controller = controller
        self._attr_unique_id = f"{entry.entry_id}_cheap_powerwall_charge_threshold"
        self._attr_native_value = controller.powerwall_charge_threshold
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
                self._controller.powerwall_charge_threshold = value

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self.async_write_ha_state()
        await self._controller.async_set_powerwall_charge_threshold(value)


class TeslaGridReleaseThresholdNumber(NumberEntity, RestoreEntity):
    _attr_has_entity_name = True
    _attr_name = "Tesla Netz-Freigabe"
    _attr_native_min_value = MIN_TESLA_GRID_RELEASE_THRESHOLD
    _attr_native_max_value = MAX_TESLA_GRID_RELEASE_THRESHOLD
    _attr_native_step = 50
    _attr_native_unit_of_measurement = "W"
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:transmission-tower-export"

    def __init__(self, controller: TeslaChargingController, entry: ConfigEntry) -> None:
        self._controller = controller
        self._attr_unique_id = f"{entry.entry_id}_tesla_grid_release_threshold"
        self._attr_native_value = controller.grid_release_threshold
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
                self._controller.grid_release_threshold = value

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self.async_write_ha_state()
        await self._controller.async_set_grid_release_threshold(value)
