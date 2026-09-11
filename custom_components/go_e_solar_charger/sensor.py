from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .cheap_controller import CheapGridChargingController
from .const import DOMAIN
from .entity import device_info
from .pv_controller import PvSurplusController
from .pv_direct_controller import PvDirectController
from .tesla_controller import TeslaChargingController
from .zoe_controller import ZoeChargeLimitController


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    controllers = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            ZoeStatusSensor(controllers["zoe"], entry),
            PvStatusSensor(controllers["pv"], entry),
            PvDirectStatusSensor(controllers["pv_direct"], entry),
            CheapStatusSensor(controllers["cheap"], entry),
            TeslaStatusSensor(controllers["tesla"], entry),
        ]
    )


class ZoeStatusSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_icon = "mdi:ev-station"
    _attr_should_poll = False

    def __init__(self, controller: ZoeChargeLimitController, entry: ConfigEntry) -> None:
        self._controller = controller
        self._attr_unique_id = f"{entry.entry_id}_zoe_status"
        self._attr_name = f"{controller.car_label} Status"
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


class CheapStatusSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Guenstigstrom Status"
    _attr_icon = "mdi:transmission-tower-export"
    _attr_should_poll = False

    def __init__(self, controller: CheapGridChargingController, entry: ConfigEntry) -> None:
        self._controller = controller
        self._attr_unique_id = f"{entry.entry_id}_cheap_status"
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


class TeslaStatusSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_icon = "mdi:car-electric"
    _attr_should_poll = False

    def __init__(self, controller: TeslaChargingController, entry: ConfigEntry) -> None:
        self._controller = controller
        self._attr_unique_id = f"{entry.entry_id}_tesla_status"
        self._attr_name = f"{controller.car_name} Ladesteuerung Status"
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

    @property
    def extra_state_attributes(self) -> dict:
        # Lets you check exactly what this integration read from the
        # Powerwall sensors and what it actually sent to go-e (the "ids"
        # payload), without having to trust the status text or dig through
        # the logs - see PvSurplusController.async_evaluate().
        read = self._controller.last_read_values
        sent = self._controller.last_pushed_values
        attrs = {
            "gelesen_solar_w": read.get("solar_w"),
            "gelesen_netz_w": read.get("grid_w"),
            "gelesen_akku_w": read.get("battery_w"),
            "gelesen_powerwall_soc": read.get("powerwall_soc"),
            "gesendet_pPv": sent.get("pPv") if sent else None,
            "gesendet_pGrid": sent.get("pGrid") if sent else None,
            "gesendet_pAkku": sent.get("pAkku") if sent else None,
        }
        if self._controller.last_pushed_at is not None:
            attrs["letzte_uebertragung"] = self._controller.last_pushed_at.isoformat()
        return attrs

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, self._controller.signal, self._handle_update)
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()


class PvDirectStatusSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_name = "PV Direktsteuerung Status"
    _attr_icon = "mdi:current-ac"
    _attr_should_poll = False

    def __init__(self, controller: PvDirectController, entry: ConfigEntry) -> None:
        self._controller = controller
        self._attr_unique_id = f"{entry.entry_id}_pv_direct_status"
        self._attr_device_info = device_info(entry)

    @property
    def native_value(self) -> str:
        return self._controller.status_text

    @property
    def extra_state_attributes(self) -> dict:
        # Same rationale as PvStatusSensor's attributes above: lets the
        # unconfirmed parts of go-e's phase-switching API (see
        # pv_direct_logic.py's module docstring) be checked against the
        # charger's actual behaviour, rather than trusted blindly.
        read = self._controller.last_read_values
        computed = self._controller.last_computed_values
        return {
            "gelesen_solar_w": read.get("solar_w"),
            "gelesen_netz_w": read.get("grid_w"),
            "gelesen_akku_w": read.get("battery_w"),
            "gelesen_powerwall_soc": read.get("powerwall_soc"),
            "berechneter_ueberschuss_w": computed.get("available_power_w"),
            "ziel_ampere": computed.get("target_amp"),
            "ziel_phasen": computed.get("target_phase"),
            "auto_laedt_wirklich": computed.get("car_actually_charging"),
            "gelesener_goe_ladezustand": computed.get("goe_car_state"),
        }

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, self._controller.signal, self._handle_update)
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()
