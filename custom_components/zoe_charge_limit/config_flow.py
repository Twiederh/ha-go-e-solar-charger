import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import selector

from .const import (
    CONF_CAR_CONNECTED_ENTITY,
    CONF_CAR_CONNECTED_ON_STATE,
    CONF_CHARGING_ENTITY,
    CONF_CHARGING_ON_STATE,
    CONF_DEFAULT_LIMIT,
    CONF_GOE_API_KEY,
    CONF_GOE_HOST,
    CONF_SOC_ENTITY,
    DEFAULT_CAR_CONNECTED_ON_STATE,
    DEFAULT_CHARGING_ON_STATE,
    DEFAULT_LIMIT,
    DOMAIN,
    MAX_LIMIT,
    MIN_LIMIT,
)


def _schema(defaults: dict) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_SOC_ENTITY, default=defaults.get(CONF_SOC_ENTITY, vol.UNDEFINED)
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Required(
                CONF_CHARGING_ENTITY, default=defaults.get(CONF_CHARGING_ENTITY, vol.UNDEFINED)
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["binary_sensor", "sensor"])
            ),
            vol.Optional(
                CONF_CHARGING_ON_STATE,
                default=defaults.get(CONF_CHARGING_ON_STATE, DEFAULT_CHARGING_ON_STATE),
            ): str,
            vol.Optional(
                CONF_CAR_CONNECTED_ENTITY,
                default=defaults.get(CONF_CAR_CONNECTED_ENTITY, vol.UNDEFINED),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["binary_sensor", "sensor"])
            ),
            vol.Optional(
                CONF_CAR_CONNECTED_ON_STATE,
                default=defaults.get(CONF_CAR_CONNECTED_ON_STATE, DEFAULT_CAR_CONNECTED_ON_STATE),
            ): str,
            vol.Required(CONF_GOE_HOST, default=defaults.get(CONF_GOE_HOST, "")): str,
            vol.Optional(
                CONF_GOE_API_KEY, default=defaults.get(CONF_GOE_API_KEY, "")
            ): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
            vol.Optional(
                CONF_DEFAULT_LIMIT, default=defaults.get(CONF_DEFAULT_LIMIT, DEFAULT_LIMIT)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=MIN_LIMIT,
                    max=MAX_LIMIT,
                    step=1,
                    mode=selector.NumberSelectorMode.SLIDER,
                    unit_of_measurement="%",
                )
            ),
        }
    )


def _normalize(user_input: dict) -> dict:
    data = dict(user_input)
    if not data.get(CONF_CAR_CONNECTED_ENTITY):
        data[CONF_CAR_CONNECTED_ENTITY] = None
    if not data.get(CONF_GOE_API_KEY):
        data[CONF_GOE_API_KEY] = ""
    data[CONF_DEFAULT_LIMIT] = int(data.get(CONF_DEFAULT_LIMIT, DEFAULT_LIMIT))
    return data


class ZoeChargeLimitConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors: dict = {}
        if user_input is not None:
            return self.async_create_entry(
                title="Zoe Ladelimit", data=_normalize(user_input)
            )
        return self.async_show_form(
            step_id="user", data_schema=_schema({}), errors=errors
        )

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry):
        return ZoeChargeLimitOptionsFlow(config_entry)


class ZoeChargeLimitOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, config_entry: ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            normalized = _normalize(user_input)
            if not user_input.get(CONF_GOE_API_KEY):
                # keep the previously stored key if the (masked) field came
                # back empty rather than wiping it out
                current = {**self.config_entry.data, **self.config_entry.options}
                normalized[CONF_GOE_API_KEY] = current.get(CONF_GOE_API_KEY, "")
            return self.async_create_entry(title="", data=normalized)

        current = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(step_id="init", data_schema=_schema(current))
