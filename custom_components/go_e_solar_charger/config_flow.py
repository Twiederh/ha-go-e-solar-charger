import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import selector

from .const import (
    CONF_CHEAP_FORECAST_ENTITY,
    CONF_CHEAP_FORECAST_THRESHOLD,
    CONF_CHEAP_GOE_PV_SWITCH_ENTITY,
    CONF_CHEAP_POWERWALL_CHARGE_THRESHOLD,
    CONF_CHEAP_PRICE_ENTITY,
    CONF_CHEAP_PRICE_THRESHOLD,
    CONF_GOE_API_KEY,
    CONF_GOE_HOST,
    CONF_PV_BATTERY_ENTITY,
    CONF_PV_DEFAULT_THRESHOLD,
    CONF_PV_EXPORT_OVERRIDE_THRESHOLD,
    CONF_PV_GRID_ENTITY,
    CONF_PV_SOC_ENTITY,
    CONF_PV_SOLAR_ENTITY,
    CONF_TESLA_CAR_NAME,
    CONF_TESLA_GRID_RELEASE_THRESHOLD,
    CONF_TESLA_SWITCH_ENTITY,
    CONF_ZOE_CAR_CONNECTED_ENTITY,
    CONF_ZOE_CAR_CONNECTED_ON_STATE,
    CONF_ZOE_CAR_NAME,
    CONF_ZOE_CHARGING_ENTITY,
    CONF_ZOE_CHARGING_ON_STATE,
    CONF_ZOE_DEFAULT_LIMIT,
    CONF_ZOE_SOC_ENTITY,
    DEFAULT_CHEAP_FORECAST_THRESHOLD,
    DEFAULT_CHEAP_POWERWALL_CHARGE_THRESHOLD,
    DEFAULT_CHEAP_PRICE_THRESHOLD,
    DEFAULT_PV_EXPORT_OVERRIDE_THRESHOLD,
    DEFAULT_PV_THRESHOLD,
    DEFAULT_TESLA_CAR_NAME,
    DEFAULT_TESLA_GRID_RELEASE_THRESHOLD,
    DEFAULT_ZOE_CAR_CONNECTED_ON_STATE,
    DEFAULT_ZOE_CAR_NAME,
    DEFAULT_ZOE_CHARGING_ON_STATE,
    DEFAULT_ZOE_LIMIT,
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

# Five steps (connection -> Zoe -> PV -> cheap-grid -> Tesla) instead of
# one giant form, now that there are four features' worth of fields to
# fill in. All five steps' answers are merged into one config entry /
# options entry at the end.


def _connection_schema(defaults: dict) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_GOE_HOST, default=defaults.get(CONF_GOE_HOST, "")): str,
            vol.Optional(
                CONF_GOE_API_KEY, default=defaults.get(CONF_GOE_API_KEY, "")
            ): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
        }
    )


def _zoe_schema(defaults: dict) -> vol.Schema:
    return vol.Schema(
        {
            vol.Optional(
                CONF_ZOE_CAR_NAME,
                default=defaults.get(CONF_ZOE_CAR_NAME, DEFAULT_ZOE_CAR_NAME),
            ): str,
            vol.Required(
                CONF_ZOE_SOC_ENTITY, default=defaults.get(CONF_ZOE_SOC_ENTITY, vol.UNDEFINED)
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Required(
                CONF_ZOE_CHARGING_ENTITY,
                default=defaults.get(CONF_ZOE_CHARGING_ENTITY, vol.UNDEFINED),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["binary_sensor", "sensor"])
            ),
            vol.Optional(
                CONF_ZOE_CHARGING_ON_STATE,
                default=defaults.get(CONF_ZOE_CHARGING_ON_STATE, DEFAULT_ZOE_CHARGING_ON_STATE),
            ): str,
            vol.Optional(
                CONF_ZOE_CAR_CONNECTED_ENTITY,
                default=defaults.get(CONF_ZOE_CAR_CONNECTED_ENTITY, vol.UNDEFINED),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["binary_sensor", "sensor"])
            ),
            vol.Optional(
                CONF_ZOE_CAR_CONNECTED_ON_STATE,
                default=defaults.get(
                    CONF_ZOE_CAR_CONNECTED_ON_STATE, DEFAULT_ZOE_CAR_CONNECTED_ON_STATE
                ),
            ): str,
            vol.Optional(
                CONF_ZOE_DEFAULT_LIMIT,
                default=defaults.get(CONF_ZOE_DEFAULT_LIMIT, DEFAULT_ZOE_LIMIT),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=MIN_ZOE_LIMIT,
                    max=MAX_ZOE_LIMIT,
                    step=1,
                    mode=selector.NumberSelectorMode.SLIDER,
                    unit_of_measurement="%",
                )
            ),
        }
    )


def _pv_schema(defaults: dict) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_PV_SOLAR_ENTITY, default=defaults.get(CONF_PV_SOLAR_ENTITY, vol.UNDEFINED)
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Required(
                CONF_PV_GRID_ENTITY, default=defaults.get(CONF_PV_GRID_ENTITY, vol.UNDEFINED)
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Required(
                CONF_PV_BATTERY_ENTITY,
                default=defaults.get(CONF_PV_BATTERY_ENTITY, vol.UNDEFINED),
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Required(
                CONF_PV_SOC_ENTITY, default=defaults.get(CONF_PV_SOC_ENTITY, vol.UNDEFINED)
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Optional(
                CONF_PV_DEFAULT_THRESHOLD,
                default=defaults.get(CONF_PV_DEFAULT_THRESHOLD, DEFAULT_PV_THRESHOLD),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=MIN_PV_THRESHOLD,
                    max=MAX_PV_THRESHOLD,
                    step=1,
                    mode=selector.NumberSelectorMode.SLIDER,
                    unit_of_measurement="%",
                )
            ),
            vol.Optional(
                CONF_PV_EXPORT_OVERRIDE_THRESHOLD,
                default=defaults.get(
                    CONF_PV_EXPORT_OVERRIDE_THRESHOLD, DEFAULT_PV_EXPORT_OVERRIDE_THRESHOLD
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=MIN_PV_EXPORT_OVERRIDE_THRESHOLD,
                    max=MAX_PV_EXPORT_OVERRIDE_THRESHOLD,
                    step=50,
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="W",
                )
            ),
        }
    )


def _cheap_schema(defaults: dict) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_CHEAP_FORECAST_ENTITY,
                default=defaults.get(CONF_CHEAP_FORECAST_ENTITY, vol.UNDEFINED),
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Required(
                CONF_CHEAP_PRICE_ENTITY,
                default=defaults.get(CONF_CHEAP_PRICE_ENTITY, vol.UNDEFINED),
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Required(
                CONF_CHEAP_GOE_PV_SWITCH_ENTITY,
                default=defaults.get(CONF_CHEAP_GOE_PV_SWITCH_ENTITY, vol.UNDEFINED),
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="switch")),
            vol.Optional(
                CONF_CHEAP_FORECAST_THRESHOLD,
                default=defaults.get(
                    CONF_CHEAP_FORECAST_THRESHOLD, DEFAULT_CHEAP_FORECAST_THRESHOLD
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=MIN_CHEAP_FORECAST_THRESHOLD,
                    max=MAX_CHEAP_FORECAST_THRESHOLD,
                    step=1,
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="kWh",
                )
            ),
            vol.Optional(
                CONF_CHEAP_PRICE_THRESHOLD,
                default=defaults.get(CONF_CHEAP_PRICE_THRESHOLD, DEFAULT_CHEAP_PRICE_THRESHOLD),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=MIN_CHEAP_PRICE_THRESHOLD,
                    max=MAX_CHEAP_PRICE_THRESHOLD,
                    step=0.5,
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="ct",
                )
            ),
            vol.Optional(
                CONF_CHEAP_POWERWALL_CHARGE_THRESHOLD,
                default=defaults.get(
                    CONF_CHEAP_POWERWALL_CHARGE_THRESHOLD,
                    DEFAULT_CHEAP_POWERWALL_CHARGE_THRESHOLD,
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=MIN_CHEAP_POWERWALL_CHARGE_THRESHOLD,
                    max=MAX_CHEAP_POWERWALL_CHARGE_THRESHOLD,
                    step=50,
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="W",
                )
            ),
        }
    )


def _tesla_schema(defaults: dict) -> vol.Schema:
    return vol.Schema(
        {
            vol.Optional(
                CONF_TESLA_CAR_NAME,
                default=defaults.get(CONF_TESLA_CAR_NAME, DEFAULT_TESLA_CAR_NAME),
            ): str,
            vol.Required(
                CONF_TESLA_SWITCH_ENTITY,
                default=defaults.get(CONF_TESLA_SWITCH_ENTITY, vol.UNDEFINED),
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="switch")),
            vol.Optional(
                CONF_TESLA_GRID_RELEASE_THRESHOLD,
                default=defaults.get(
                    CONF_TESLA_GRID_RELEASE_THRESHOLD, DEFAULT_TESLA_GRID_RELEASE_THRESHOLD
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=MIN_TESLA_GRID_RELEASE_THRESHOLD,
                    max=MAX_TESLA_GRID_RELEASE_THRESHOLD,
                    step=50,
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="W",
                )
            ),
        }
    )


def _normalize(data: dict) -> dict:
    data = dict(data)
    if not data.get(CONF_ZOE_CAR_CONNECTED_ENTITY):
        data[CONF_ZOE_CAR_CONNECTED_ENTITY] = None
    if not data.get(CONF_GOE_API_KEY):
        data[CONF_GOE_API_KEY] = ""
    # Free-text car names - blank (or whitespace-only) falls back to the
    # default rather than storing an empty display name.
    data[CONF_ZOE_CAR_NAME] = str(data.get(CONF_ZOE_CAR_NAME) or "").strip() or DEFAULT_ZOE_CAR_NAME
    data[CONF_TESLA_CAR_NAME] = (
        str(data.get(CONF_TESLA_CAR_NAME) or "").strip() or DEFAULT_TESLA_CAR_NAME
    )
    if CONF_ZOE_DEFAULT_LIMIT in data:
        data[CONF_ZOE_DEFAULT_LIMIT] = int(data[CONF_ZOE_DEFAULT_LIMIT])
    if CONF_PV_DEFAULT_THRESHOLD in data:
        data[CONF_PV_DEFAULT_THRESHOLD] = int(data[CONF_PV_DEFAULT_THRESHOLD])
    if CONF_PV_EXPORT_OVERRIDE_THRESHOLD in data:
        data[CONF_PV_EXPORT_OVERRIDE_THRESHOLD] = int(data[CONF_PV_EXPORT_OVERRIDE_THRESHOLD])
    if CONF_CHEAP_FORECAST_THRESHOLD in data:
        data[CONF_CHEAP_FORECAST_THRESHOLD] = int(data[CONF_CHEAP_FORECAST_THRESHOLD])
    if CONF_CHEAP_PRICE_THRESHOLD in data:
        data[CONF_CHEAP_PRICE_THRESHOLD] = float(data[CONF_CHEAP_PRICE_THRESHOLD])
    if CONF_CHEAP_POWERWALL_CHARGE_THRESHOLD in data:
        data[CONF_CHEAP_POWERWALL_CHARGE_THRESHOLD] = int(
            data[CONF_CHEAP_POWERWALL_CHARGE_THRESHOLD]
        )
    if CONF_TESLA_GRID_RELEASE_THRESHOLD in data:
        data[CONF_TESLA_GRID_RELEASE_THRESHOLD] = int(data[CONF_TESLA_GRID_RELEASE_THRESHOLD])
    return data


class GoESolarChargerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._data: dict = {}

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_zoe()
        return self.async_show_form(step_id="user", data_schema=_connection_schema({}))

    async def async_step_zoe(self, user_input=None):
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_pv()
        return self.async_show_form(step_id="zoe", data_schema=_zoe_schema({}))

    async def async_step_pv(self, user_input=None):
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_cheap()
        return self.async_show_form(step_id="pv", data_schema=_pv_schema({}))

    async def async_step_cheap(self, user_input=None):
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_tesla()
        return self.async_show_form(step_id="cheap", data_schema=_cheap_schema({}))

    async def async_step_tesla(self, user_input=None):
        if user_input is not None:
            self._data.update(user_input)
            return self.async_create_entry(
                title="go-e Solar Charger", data=_normalize(self._data)
            )
        return self.async_show_form(step_id="tesla", data_schema=_tesla_schema({}))

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry):
        return GoESolarChargerOptionsFlow(config_entry)


class GoESolarChargerOptionsFlow(config_entries.OptionsFlow):
    # Deliberately NOT storing the entry as `self.config_entry`: recent Home
    # Assistant versions turned that name into a read-only property that HA
    # itself populates, and a custom integration assigning to it in
    # __init__ now raises AttributeError (seen as a generic 500 "Server got
    # itself in trouble" when opening "Configure"). Keeping our own
    # `_entry` attribute avoids the collision on every HA version.
    def __init__(self, config_entry: ConfigEntry) -> None:
        self._entry = config_entry
        self._data: dict = {}

    @property
    def _current(self) -> dict:
        return {**self._entry.data, **self._entry.options}

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            self._data.update(user_input)
            if not user_input.get(CONF_GOE_API_KEY):
                self._data[CONF_GOE_API_KEY] = self._current.get(CONF_GOE_API_KEY, "")
            return await self.async_step_zoe()
        return self.async_show_form(step_id="init", data_schema=_connection_schema(self._current))

    async def async_step_zoe(self, user_input=None):
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_pv()
        return self.async_show_form(step_id="zoe", data_schema=_zoe_schema(self._current))

    async def async_step_pv(self, user_input=None):
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_cheap()
        return self.async_show_form(step_id="pv", data_schema=_pv_schema(self._current))

    async def async_step_cheap(self, user_input=None):
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_tesla()
        return self.async_show_form(step_id="cheap", data_schema=_cheap_schema(self._current))

    async def async_step_tesla(self, user_input=None):
        if user_input is not None:
            self._data.update(user_input)
            return self.async_create_entry(title="", data=_normalize(self._data))
        return self.async_show_form(step_id="tesla", data_schema=_tesla_schema(self._current))
