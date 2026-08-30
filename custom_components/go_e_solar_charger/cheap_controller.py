"""Glue between Home Assistant state and the pure logic in cheap_logic.py.

Internal tracking state (_next_day_*, _today_*, _suppressing, _was_cheap,
_zoe_charging, _tesla_charging, _powerwall_was_charging) is kept up to date
regardless of whether the feature is enabled, so a re-enable can catch up
immediately instead of waiting for the next evening/midnight cycle. Only
the actual HA/go-e/Tesla side effects are gated by `self.enabled`.
"""
import logging
from typing import Optional

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_change

from .cheap_logic import (
    ACTION_ENTER_LOW_SOLAR_DAY,
    ACTION_EXIT_LOW_SOLAR_DAY,
    CarChargeResult,
    DailyRolloverInput,
    ForecastDecisionInput,
    PowerwallChargingEdgeInput,
    PriceWindowInput,
    WindowEdgeInput,
    decide_daily_rollover,
    decide_powerwall_arbitration,
    decide_window_edge,
    is_cheap_now,
    is_low_solar_day,
    status_text,
)
from .const import (
    CHEAP_FORECAST_EVAL_HOUR,
    CHEAP_FORECAST_EVAL_MINUTE,
    CONF_CHEAP_FORECAST_ENTITY,
    CONF_CHEAP_FORECAST_THRESHOLD,
    CONF_CHEAP_GOE_PV_SWITCH_ENTITY,
    CONF_CHEAP_POWERWALL_CHARGE_THRESHOLD,
    CONF_CHEAP_PRICE_ENTITY,
    CONF_CHEAP_PRICE_THRESHOLD,
    CONF_GOE_API_KEY,
    CONF_GOE_HOST,
    CONF_PV_BATTERY_ENTITY,
    CONF_ZOE_CAR_CONNECTED_ENTITY,
    CONF_ZOE_CAR_CONNECTED_ON_STATE,
    DEFAULT_CHEAP_FORECAST_THRESHOLD,
    DEFAULT_CHEAP_POWERWALL_CHARGE_THRESHOLD,
    DEFAULT_CHEAP_PRICE_THRESHOLD,
    DEFAULT_TESLA_CAR_NAME,
    DEFAULT_ZOE_CAR_CONNECTED_ON_STATE,
    DEFAULT_ZOE_CAR_NAME,
    SIGNAL_CHEAP_STATUS_UPDATE,
)
from .goe_client import GoEClient

_LOGGER = logging.getLogger(__name__)

NOT_CONFIGURED_TEXT = (
    'Nicht konfiguriert - bitte unter "Konfigurieren" Solar-Vorhersage, '
    "Strompreis und go-e-PV-Schalter angeben."
)


class CheapGridChargingController:
    """One instance per config entry. On days with a poor solar forecast,
    suppresses PV-surplus charging entirely (for the go-e car and, if
    configured, the Tesla's own PV/export-based gating) and instead
    force-charges both from the grid during the configured cheap price
    window - unless the Powerwall itself is charging from the grid, in
    which case only one of the two may charge at a time, per priority."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        on_frc_changed=None,
        tesla_controller=None,
        zoe_controller=None,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self._on_frc_changed = on_frc_changed
        self._tesla_controller = tesla_controller
        self._zoe_controller = zoe_controller
        config = {**entry.data, **entry.options}
        self._forecast_entity = config.get(CONF_CHEAP_FORECAST_ENTITY)
        self._price_entity = config.get(CONF_CHEAP_PRICE_ENTITY)
        self._goe_pv_switch_entity = config.get(CONF_CHEAP_GOE_PV_SWITCH_ENTITY)
        self._car_connected_entity = config.get(CONF_ZOE_CAR_CONNECTED_ENTITY)
        self._car_connected_on_state = config.get(
            CONF_ZOE_CAR_CONNECTED_ON_STATE, DEFAULT_ZOE_CAR_CONNECTED_ON_STATE
        )
        self._battery_entity = config.get(CONF_PV_BATTERY_ENTITY)
        self._configured = bool(
            self._forecast_entity and self._price_entity and self._goe_pv_switch_entity
        )
        self._goe = GoEClient(
            async_get_clientsession(hass),
            config[CONF_GOE_HOST],
            config.get(CONF_GOE_API_KEY, ""),
        )

        # Set from restored entity state right after platform setup, before
        # async_setup() runs - see __init__.py.
        self.forecast_threshold: float = config.get(
            CONF_CHEAP_FORECAST_THRESHOLD, DEFAULT_CHEAP_FORECAST_THRESHOLD
        )
        self.price_threshold: float = config.get(
            CONF_CHEAP_PRICE_THRESHOLD, DEFAULT_CHEAP_PRICE_THRESHOLD
        )
        self.powerwall_charge_threshold: float = config.get(
            CONF_CHEAP_POWERWALL_CHARGE_THRESHOLD, DEFAULT_CHEAP_POWERWALL_CHARGE_THRESHOLD
        )
        # Display label for this car's own entities/status text - e.g.
        # "Zoe Ladelimit" if the go-e car was named "Zoe". Falls back to
        # the default name if the controller wasn't passed in (shouldn't
        # happen outside tests exercising this controller in isolation).
        self.zoe_car_label: str = (
            self._zoe_controller.car_label
            if self._zoe_controller is not None
            else f"{DEFAULT_ZOE_CAR_NAME} Ladelimit"
        )
        self.zoe_car_name: str = (
            self._zoe_controller.car_name
            if self._zoe_controller is not None
            else DEFAULT_ZOE_CAR_NAME
        )
        self.tesla_car_label: str = (
            self._tesla_controller.car_name
            if self._tesla_controller is not None
            else DEFAULT_TESLA_CAR_NAME
        )
        # Priority option labels use the plain car name (not the
        # "<name> Ladelimit" entity-label form) - "Zoe zuerst" reads better
        # than "Zoe Ladelimit zuerst" once the car has an actual name
        # rather than the generic default "Auto".
        self.priority_option_zoe_first: str = f"{self.zoe_car_name} zuerst"
        self.priority_option_tesla_first: str = f"{self.tesla_car_label} zuerst"
        # Live-adjustable via select.py, not part of the config flow.
        self.car_priority: str = self.priority_option_zoe_first
        self.enabled: bool = True
        self.status_text: str = "Initialisiere ..."

        self._next_day_low_solar: bool = False
        self._next_day_forecast_kwh: Optional[float] = None
        self._today_low_solar: bool = False
        self._today_forecast_kwh: Optional[float] = None
        self._suppressing: bool = False
        self._zoe_charging: bool = False
        self._tesla_charging: bool = False
        self._was_cheap: Optional[bool] = None
        self._powerwall_was_charging: Optional[bool] = None

        self._unsub_price = None
        self._unsub_battery = None
        self._unsub_eval_timer = None

    @property
    def signal(self) -> str:
        return f"{SIGNAL_CHEAP_STATUS_UPDATE}_{self.entry.entry_id}"

    @property
    def suppress_pv(self) -> bool:
        """True while this feature has taken over for the day - the
        PV-surplus feature should not send anything at all while this is
        true, not even the zeroed safety values."""
        return self.enabled and self._suppressing

    @property
    def suppress_tesla(self) -> bool:
        """True while this feature has taken over for the day - the
        Tesla's own PV/export-based gating should stay inert while this is
        true, since this controller drives the Tesla switch directly."""
        return self.enabled and self._suppressing

    async def async_setup(self) -> None:
        if not self._configured:
            self.status_text = NOT_CONFIGURED_TEXT
            async_dispatcher_send(self.hass, self.signal)
            return

        self._unsub_price = async_track_state_change_event(
            self.hass, [self._price_entity], self._handle_price_event
        )
        if self._battery_entity:
            self._unsub_battery = async_track_state_change_event(
                self.hass, [self._battery_entity], self._handle_battery_event
            )
        self._unsub_eval_timer = async_track_time_change(
            self.hass,
            self._handle_evening_eval,
            hour=CHEAP_FORECAST_EVAL_HOUR,
            minute=CHEAP_FORECAST_EVAL_MINUTE,
            second=0,
        )
        # Baseline reads so the very first real changes are correctly
        # classified as edges (or not) instead of being compared against
        # "unknown".
        self._was_cheap = is_cheap_now(self._read_price_input())
        self._powerwall_was_charging = self._read_powerwall_charging()
        self._refresh_status()

    def async_unload(self) -> None:
        if self._unsub_price:
            self._unsub_price()
            self._unsub_price = None
        if self._unsub_battery:
            self._unsub_battery()
            self._unsub_battery = None
        if self._unsub_eval_timer:
            self._unsub_eval_timer()
            self._unsub_eval_timer = None

    # --- sensor reads ----------------------------------------------------

    def _read_float(self, entity_id) -> Optional[float]:
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None
        try:
            return float(state.state)
        except (TypeError, ValueError):
            return None

    def _read_car_connected(self) -> Optional[bool]:
        if not self._car_connected_entity:
            return None
        state = self.hass.states.get(self._car_connected_entity)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None
        return state.state.strip().lower() == self._car_connected_on_state.strip().lower()

    def _read_price_input(self) -> PriceWindowInput:
        return PriceWindowInput(self._read_float(self._price_entity), self.price_threshold)

    def _read_powerwall_charging(self) -> bool:
        """True while the Powerwall itself is drawing more than the
        configured threshold from the grid/PV to charge itself. When the
        battery sensor is unavailable, conservatively assume it IS charging
        - risking an unnecessarily paused car is safer than risking an
        overload by letting both charge when we can't actually tell."""
        battery_w = self._read_float(self._battery_entity)
        if battery_w is None:
            return True
        charging_w = -battery_w
        return charging_w > self.powerwall_charge_threshold

    def _tesla_configured(self) -> bool:
        return self._tesla_controller is not None and self._tesla_controller.configured

    def _zoe_has_priority(self) -> bool:
        return self.car_priority != self.priority_option_tesla_first

    def _window_edge_input(self, *, entering: bool, leaving: bool) -> WindowEdgeInput:
        return WindowEdgeInput(
            entering=entering,
            leaving=leaving,
            low_solar_today=self._today_low_solar,
            car_connected=self._read_car_connected(),
            tesla_configured=self._tesla_configured(),
            powerwall_charging=self._read_powerwall_charging(),
            zoe_has_priority=self._zoe_has_priority(),
        )

    # --- event handlers ----------------------------------------------------

    @callback
    def _handle_evening_eval(self, now) -> None:
        self.hass.async_create_task(self._async_evening_eval())

    async def _async_evening_eval(self) -> None:
        forecast_kwh = self._read_float(self._forecast_entity)
        decision = is_low_solar_day(ForecastDecisionInput(forecast_kwh, self.forecast_threshold))
        if decision is not None:
            self._next_day_low_solar = decision
            self._next_day_forecast_kwh = forecast_kwh
        self._refresh_status()

    @callback
    def _handle_price_event(self, event: Event) -> None:
        self.hass.async_create_task(self._async_handle_price_change())

    async def _async_handle_price_change(self) -> None:
        now_cheap = is_cheap_now(self._read_price_input())
        if now_cheap is None:
            self._refresh_status()
            return

        was_cheap = bool(self._was_cheap)
        entering = now_cheap and not was_cheap
        leaving = was_cheap and not now_cheap
        self._was_cheap = now_cheap

        if entering:
            # The price window opens exactly at local midnight in this
            # setup, which conveniently is also the right moment to roll
            # yesterday evening's latched decision over into "today"'s.
            self._today_low_solar = self._next_day_low_solar
            self._today_forecast_kwh = self._next_day_forecast_kwh
            rollover_action = decide_daily_rollover(
                DailyRolloverInput(
                    was_suppressing=self._suppressing, low_solar_today=self._today_low_solar
                )
            )
            await self._apply_rollover_action(rollover_action)

        edge_result = decide_window_edge(self._window_edge_input(entering=entering, leaving=leaving))
        if edge_result is not None:
            await self._apply_car_charging(
                edge_result, zoe_stop_mode="neutral" if leaving else "off"
            )
        self._refresh_status()

    @callback
    def _handle_battery_event(self, event: Event) -> None:
        self.hass.async_create_task(self._async_handle_battery_change())

    async def _async_handle_battery_change(self) -> None:
        # Only meaningful while the forced window is actually open on a
        # low-solar day - otherwise there's nothing being arbitrated.
        if not (self._today_low_solar and bool(self._was_cheap)):
            return
        now_charging = self._read_powerwall_charging()
        was_charging = bool(self._powerwall_was_charging)
        self._powerwall_was_charging = now_charging
        if now_charging == was_charging:
            return

        result = decide_powerwall_arbitration(
            PowerwallChargingEdgeInput(
                powerwall_charging=now_charging,
                was_charging=was_charging,
                zoe_charging=self._zoe_charging,
                tesla_charging=self._tesla_charging,
                zoe_wants=bool(self._read_car_connected()),
                tesla_wants=self._tesla_configured(),
                zoe_has_priority=self._zoe_has_priority(),
            )
        )
        if result is not None:
            # A mid-window pause must actually stop the go-e, not just hand
            # it back to Auto-Ladelimit's own SoC logic.
            await self._apply_car_charging(result, zoe_stop_mode="off")
        self._refresh_status()

    # --- action application ----------------------------------------------

    async def _apply_rollover_action(self, action) -> None:
        if action == ACTION_ENTER_LOW_SOLAR_DAY:
            self._suppressing = True
            if self.enabled:
                await self._set_goe_pv_switch(False)
        elif action is not None:  # ACTION_EXIT_LOW_SOLAR_DAY
            self._suppressing = False
            if self.enabled:
                await self._set_goe_pv_switch(True)
        if action is not None and self._tesla_controller is not None:
            # Let the Tesla controller's own (suppression-aware) evaluate()
            # immediately reflect the new suppression state, rather than
            # waiting for its next sensor change.
            await self._tesla_controller.async_evaluate()

    async def _apply_zoe_charging(self, should_charge: bool, *, stop_mode: str) -> None:
        if should_charge == self._zoe_charging:
            return
        self._zoe_charging = should_charge
        if not self.enabled:
            return
        try:
            if should_charge:
                await self._goe.force_charging_on()
            elif stop_mode == "neutral":
                await self._goe.release()
            else:
                await self._goe.stop_charging()
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning(
                "Konnte Guenstigstrom-Laden (%s) nicht umschalten: %s", self.zoe_car_label, exc
            )
        if self._on_frc_changed:
            await self._on_frc_changed()

    async def _apply_tesla_charging(self, should_charge: bool) -> None:
        if should_charge == self._tesla_charging:
            return
        self._tesla_charging = should_charge
        if self.enabled and self._tesla_controller is not None:
            await self._tesla_controller.async_force_charge(should_charge)

    async def _apply_car_charging(self, result: CarChargeResult, *, zoe_stop_mode: str = "off") -> None:
        await self._apply_zoe_charging(result.zoe_should_charge, stop_mode=zoe_stop_mode)
        await self._apply_tesla_charging(result.tesla_should_charge)

    async def _set_goe_pv_switch(self, on: bool) -> None:
        try:
            await self.hass.services.async_call(
                "switch",
                "turn_on" if on else "turn_off",
                {"entity_id": self._goe_pv_switch_entity},
                blocking=True,
            )
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning(
                "Konnte %s nicht %s: %s",
                self._goe_pv_switch_entity,
                "einschalten" if on else "ausschalten",
                exc,
            )

    async def _async_resync(self) -> None:
        """Applies whatever should currently be true given today's already-
        latched decision and the live price/car/Powerwall state, as if a
        window edge just happened - used when (re-)enabling or on a manual
        test, not on every price tick (that would fight the SoC-limit
        stop)."""
        now_cheap = is_cheap_now(self._read_price_input())
        rollover_action = decide_daily_rollover(
            DailyRolloverInput(was_suppressing=self._suppressing, low_solar_today=self._today_low_solar)
        )
        await self._apply_rollover_action(rollover_action)
        edge_result = decide_window_edge(self._window_edge_input(entering=bool(now_cheap), leaving=False))
        if edge_result is not None:
            await self._apply_car_charging(edge_result, zoe_stop_mode="off")
        self._was_cheap = now_cheap
        self._powerwall_was_charging = self._read_powerwall_charging()

    def _refresh_status(self) -> None:
        self.status_text = status_text(
            enabled=self.enabled,
            forecast_kwh=self._today_forecast_kwh,
            threshold_kwh=self.forecast_threshold,
            low_solar_today=self._today_low_solar,
            cheap_now=bool(self._was_cheap),
            car_connected=self._read_car_connected(),
            tesla_configured=self._tesla_configured(),
            zoe_charging=self._zoe_charging,
            tesla_charging=self._tesla_charging,
            powerwall_charging=self._read_powerwall_charging(),
            zoe_car_label=self.zoe_car_label,
            tesla_car_label=self.tesla_car_label,
        )
        async_dispatcher_send(self.hass, self.signal)

    # --- entity-facing API ----------------------------------------------

    async def async_set_forecast_threshold(self, value: float) -> None:
        self.forecast_threshold = value
        self._refresh_status()

    async def async_set_price_threshold(self, value: float) -> None:
        self.price_threshold = value
        self._refresh_status()

    async def async_set_powerwall_charge_threshold(self, value: float) -> None:
        self.powerwall_charge_threshold = value
        self._refresh_status()

    async def async_set_car_priority(self, value: str) -> None:
        self.car_priority = value
        if self._configured and self.enabled and self._today_low_solar and bool(self._was_cheap):
            edge_result = decide_window_edge(self._window_edge_input(entering=True, leaving=False))
            if edge_result is not None:
                await self._apply_car_charging(edge_result, zoe_stop_mode="off")
        self._refresh_status()

    async def async_set_enabled(self, value: bool) -> None:
        if not self._configured:
            self.enabled = value
            self.status_text = NOT_CONFIGURED_TEXT
            async_dispatcher_send(self.hass, self.signal)
            return

        was_enabled = self.enabled
        if not value and was_enabled:
            # Hand control back immediately rather than leaving the go-e
            # mid-forced-charge or the PV switch off with nothing left to
            # ever turn it back on. This must run *before* `self.enabled`
            # is flipped off, since _apply_zoe_charging/_apply_rollover_
            # action only perform their actual go-e/switch calls while
            # `self.enabled` is still true.
            if self._zoe_charging:
                await self._apply_zoe_charging(False, stop_mode="neutral")
            if self._tesla_charging:
                # Don't force the Tesla off - EXIT_LOW_SOLAR_DAY below hands
                # control back to its own logic via async_evaluate() instead.
                self._tesla_charging = False
            if self._suppressing:
                await self._apply_rollover_action(ACTION_EXIT_LOW_SOLAR_DAY)
        self.enabled = value
        if value and not was_enabled:
            await self._async_resync()
        self._refresh_status()

    async def async_manual_test(self) -> None:
        """Re-samples the forecast right now (instead of waiting for the
        daily evaluation time) and re-applies the current window state -
        useful to verify the go-e/switch connections without waiting for
        the schedule."""
        if not self._configured:
            return
        forecast_kwh = self._read_float(self._forecast_entity)
        decision = is_low_solar_day(ForecastDecisionInput(forecast_kwh, self.forecast_threshold))
        if decision is not None:
            self._next_day_low_solar = decision
            self._next_day_forecast_kwh = forecast_kwh
            self._today_low_solar = decision
            self._today_forecast_kwh = forecast_kwh
        await self._async_resync()
        self._refresh_status()
