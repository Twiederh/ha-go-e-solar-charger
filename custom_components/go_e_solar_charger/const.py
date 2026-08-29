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
# The Powerwall itself sometimes exports well above this even while below
# its own SoC threshold (e.g. around midday in summer, to avoid sitting at
# 100 % too long) - in that case forward the real pPv/pGrid/pAkku values to
# go-e anyway instead of the zeroed safety values.
CONF_PV_EXPORT_OVERRIDE_THRESHOLD = "pv_export_override_threshold_w"

DEFAULT_PV_THRESHOLD = 50
MIN_PV_THRESHOLD = 0
MAX_PV_THRESHOLD = 100
DEFAULT_PV_EXPORT_OVERRIDE_THRESHOLD = 3100
MIN_PV_EXPORT_OVERRIDE_THRESHOLD = 0
MAX_PV_EXPORT_OVERRIDE_THRESHOLD = 20000
# go-e expects pPv/pGrid/pAkku to be refreshed at least every 5 seconds -
# if it doesn't see an update in time it assumes the PV-surplus source
# went away and pauses charging as a safety fallback. So we re-push on a
# timer with margin below that, on top of pushing immediately whenever a
# source sensor changes.
PV_PUSH_KEEPALIVE_INTERVAL_SECONDS = 4

# --- Tesla charge gating feature (plain on/off switch, gated by the same
# Powerwall SoC/grid sensors already configured for the PV-surplus push
# feature above, reusing its *live* threshold - with its own, lower,
# grid-export override). The Tesla has its own solar-aware charging
# solution and its own charge limit (number.tesla_ladelimit) outside this
# integration's scope; all this feature does is start/stop it. ---
CONF_TESLA_SWITCH_ENTITY = "tesla_switch_entity_id"
CONF_TESLA_GRID_RELEASE_THRESHOLD = "tesla_grid_release_threshold_w"

DEFAULT_TESLA_GRID_RELEASE_THRESHOLD = 1400
MIN_TESLA_GRID_RELEASE_THRESHOLD = 0
MAX_TESLA_GRID_RELEASE_THRESHOLD = 20000

# --- Cheap-grid charging feature (skip PV, force-charge from the grid on
# days with a poor solar forecast, during the cheap price window) ---
CONF_CHEAP_FORECAST_ENTITY = "cheap_forecast_entity_id"
CONF_CHEAP_PRICE_ENTITY = "cheap_price_entity_id"
CONF_CHEAP_GOE_PV_SWITCH_ENTITY = "cheap_goe_pv_switch_entity_id"
CONF_CHEAP_FORECAST_THRESHOLD = "cheap_forecast_threshold_kwh"
CONF_CHEAP_PRICE_THRESHOLD = "cheap_price_threshold_ct"

DEFAULT_CHEAP_FORECAST_THRESHOLD = 30
MIN_CHEAP_FORECAST_THRESHOLD = 0
MAX_CHEAP_FORECAST_THRESHOLD = 100
# Between the two-tier price values a typical fixed off-peak tariff (e.g.
# Octopus Go-style) swings between - just needs to sit clearly between the
# cheap and expensive rate, exact value doesn't matter and is adjustable.
DEFAULT_CHEAP_PRICE_THRESHOLD = 20
MIN_CHEAP_PRICE_THRESHOLD = 0
MAX_CHEAP_PRICE_THRESHOLD = 100

# Fixed daily time (local) at which "is tomorrow a low-solar day" gets
# latched from the forecast sensor's current reading. Needs to be well
# before midnight: forecast integrations typically roll their "tomorrow"
# slot forward to the next day right at midnight, at which point the same
# sensor stops describing the day this decision is actually for.
CHEAP_FORECAST_EVAL_HOUR = 20
CHEAP_FORECAST_EVAL_MINUTE = 30

# go-e local API v2 "frc" (forceState) values - see
# https://github.com/goecharger/go-eCharger-API-v2
FRC_NEUTRAL = 0  # let go-e's own charging logic decide
FRC_OFF = 1  # force charging off, regardless of what the normal logic wants
FRC_ON = 2  # force charging on, regardless of PV surplus or amp settings

PLATFORMS = ["number", "switch", "sensor", "button"]

SIGNAL_ZOE_STATUS_UPDATE = f"{DOMAIN}_zoe_status_update"
SIGNAL_PV_STATUS_UPDATE = f"{DOMAIN}_pv_status_update"
SIGNAL_CHEAP_STATUS_UPDATE = f"{DOMAIN}_cheap_status_update"
SIGNAL_TESLA_STATUS_UPDATE = f"{DOMAIN}_tesla_status_update"
