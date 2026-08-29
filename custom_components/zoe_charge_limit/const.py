"""Constants for the Zoe Ladelimit (go-e) integration."""

DOMAIN = "zoe_charge_limit"

CONF_SOC_ENTITY = "soc_entity_id"
CONF_CHARGING_ENTITY = "charging_entity_id"
CONF_CHARGING_ON_STATE = "charging_on_state"
CONF_CAR_CONNECTED_ENTITY = "car_connected_entity_id"
CONF_CAR_CONNECTED_ON_STATE = "car_connected_on_state"
CONF_GOE_HOST = "goe_host"
CONF_GOE_API_KEY = "goe_api_key"
CONF_DEFAULT_LIMIT = "default_limit"

DEFAULT_CHARGING_ON_STATE = "on"
DEFAULT_CAR_CONNECTED_ON_STATE = "on"
DEFAULT_LIMIT = 80
MIN_LIMIT = 20
MAX_LIMIT = 100

# go-e local API v2 "frc" (forceState) values - see
# https://github.com/goecharger/go-eCharger-API-v2
FRC_NEUTRAL = 0  # let go-e's own charging logic decide
FRC_OFF = 1  # force charging off, regardless of what the normal logic wants

PLATFORMS = ["number", "switch", "sensor", "button"]

SIGNAL_STATUS_UPDATE = f"{DOMAIN}_status_update"
