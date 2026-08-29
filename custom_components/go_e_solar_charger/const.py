"""Constants for the go-e Solar Charger integration."""

DOMAIN = "go_e_solar_charger"

# --- shared (go-e connection) ---
CONF_GOE_HOST = "goe_host"
CONF_GOE_API_KEY = "goe_api_key"

# --- Zoe charge limit feature (frc = force charging off/on) ---
CONF_ZOE_SOC_ENTITY = "zoe_soc_entity_id"
CONF_ZOE_CHARGING_ENTITY = "zoe_charging_entity_id"
CONF_ZOE_CHARGING_ON_STATE = "zoe_charging_on_state"
CONF_ZOE_CAR_CONNECTED_ENTITY = "zoe_car_connected_entity_id"
CONF_ZOE_CAR_CONNECTED_ON_STATE = "zoe_car_connected_on_state"
CONF_ZOE_DEFAULT_LIMIT = "zoe_default_limit"

DEFAULT_ZOE_CHARGING_ON_STATE = "on"
DEFAULT_ZOE_CAR_CONNECTED_ON_STATE = "on"
DEFAULT_ZOE_LIMIT = 80
MIN_ZOE_LIMIT = 20
MAX_ZOE_LIMIT = 100

# --- PV-surplus push feature (ids = pPv/pGrid/pAkku, gated by Powerwall SoC) ---
CONF_PV_SOLAR_ENTITY = "pv_solar_entity_id"
CONF_PV_GRID_ENTITY = "pv_grid_entity_id"
CONF_PV_BATTERY_ENTITY = "pv_battery_entity_id"
CONF_PV_SOC_ENTITY = "pv_soc_entity_id"
CONF_PV_DEFAULT_THRESHOLD = "pv_default_threshold"

DEFAULT_PV_THRESHOLD = 50
MIN_PV_THRESHOLD = 0
MAX_PV_THRESHOLD = 100
# go-e expects pPv/pGrid/pAkku to be refreshed at least every 5 seconds -
# if it doesn't see an update in time it assumes the PV-surplus source
# went away and pauses charging as a safety fallback. So we re-push on a
# timer with margin below that, on top of pushing immediately whenever a
# source sensor changes.
PV_PUSH_KEEPALIVE_INTERVAL_SECONDS = 4

# go-e local API v2 "frc" (forceState) values - see
# https://github.com/goecharger/go-eCharger-API-v2
FRC_NEUTRAL = 0  # let go-e's own charging logic decide
FRC_OFF = 1  # force charging off, regardless of what the normal logic wants

PLATFORMS = ["number", "switch", "sensor", "button"]

SIGNAL_ZOE_STATUS_UPDATE = f"{DOMAIN}_zoe_status_update"
SIGNAL_PV_STATUS_UPDATE = f"{DOMAIN}_pv_status_update"
