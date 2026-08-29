"""Glue between Home Assistant state and the pure logic in cheap_logic.py.

Internal tracking state (_next_day_*, _today_*, _suppressing, _was_cheap)
is kept up to date regardless of whether the feature is enabled, so a
re-enable can catch up immediately instead of waiting for the next
evening/midnight cycle. Only the actual HA/go-e side effects are gated by
`self.enabled`.
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
    ACTION_START_FORCED_CHARGE,
    ACTION_STOP_FORCED_CHARGE,
    DailyRolloverInput,
    ForecastDecisionInput,
    PriceWindowInput,
    WindowEdgeInput,
    decide_daily_rollover,
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
    CONF_CHEAP_PRICE_ENTITY,
    CONF_CHEAP_PRICE_THRESHOLD,
    CONF_GOE_API_KEY,
    CONF_GOE_HOST,
    CONF_ZOE_CAR_CONNECTED_ENTITY,
    CONF_ZOE_CAR_CONNECTED_ON_STATE,
    DEFAULT_CHEAP_FORECAST_THRESHOLD,
    DEFAULT_CHEAP_PRICE_THRESHOLD,
    DEFAULT_ZOE_CAR_CONNECTED_ON_STATE,
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
    suppresses PV-surplus charging entirely and instead force-charges from
    the grid during the configured cheap price window."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        on_frc_changed=None,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self._on_frc_changed = on_frc_changed
        config = {**entry.data, **entry.options}
        self._forecast_entity = config.get(CONF_CHEAP_FORECAST_ENTITY)
        self._price_entity = config.get(CONF_CHEAP_PRICE_ENTITY)
        self._goe_pv_switch_entity = config.get(CONF_CHEAP_GOE_PV_SWITCH_ENTITY)
        self._car_connected_entity = config.get(CONF_ZOE_CAR_CONNECTED_ENTITY)
        self._car_connected_on_state = config.get(
            CONF_ZOE_CAR_CONNECTED_ON_STATE, DEFAULT_ZOE_CAR_CONNECTED_ON_STATE
        )
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
        self.enabled: bool = True
        self.status_text: str = "Initialisiere ..."

        self._next_day_low_solar: bool = False
        self._next_day_forecast_kwh: Optional[float] = None
        self._today_low_solar: bool = False
        self._today_forecast_kwh: Optional[float] = None
        self._suppressing: bool = False
        self._forced_active: bool = False
        self._was_cheap: Optional[bool] = None

        self._unsub_price = None
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

    async def async_setup(self) -> None:
        if not self._configured:
            self.status_text = NOT_CONFIGURED_TEXT
            async_dispatcher_send(self.hass, self.signal)
            return

        self._unsub_price = async_track_state_change_event(
            self.hass, [self._price_entity], self._handle_price_event
        )
        self._unsub_eval_timer = async_track_time_change(
            self.hass,
            self._handle_evening_eval,
            hour=CHEAP_FORECAST_EVAL_HOUR,
            minute=CHEAP_FORECAST_EVAL_MINUTE,
            second=0,
        )
        # Baseline read so the very first real price change is correctly
        # classified as an edge (or not) instead of being compared against
        # "unknown".
        self._was_cheap = is_cheap_now(self._read_price_input())
        self._refresh_status()

    def async_unload(self) -> None:
        if self._unsub_price:
            self._unsub_price()
            self._unsub_price = None
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

        edge_action = decide_window_edge(
            WindowEdgeInput(
                entering=entering,
                leaving=leaving,
                low_solar_today=self._today_low_solar,
                car_connected=self._read_car_connected(),
            )
        )
        await self._apply_window_action(edge_action)
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

    async def _apply_window_action(self, action) -> None:
        if action == ACTION_START_FORCED_CHARGE:
            self._forced_active = True
            if self.enabled:
                try:
                    await self._goe.force_charging_on()
                except Exception as exc:  # noqa: BLE001
                    _LOGGER.warning("Konnte Guenstigstrom-Laden nicht erzwingen: %s", exc)
                if self._on_frc_changed:
                    await self._on_frc_changed()
        elif action is not None:  # ACTION_STOP_FORCED_CHARGE
            self._forced_active = False
            if self.enabled:
                try:
                    await self._goe.release()
                except Exception as exc:  # noqa: BLE001
                    _LOGGER.warning("Konnte go-e nach Guenstigstrom-Fenster nicht freigeben: %s", exc)
                if self._on_frc_changed:
                    await self._on_frc_changed()

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
        latched decision and the live price/car state, as if a window edge
        just happened - used when (re-)enabling or on a manual test, not on
        every price tick (that would fight the SoC-limit stop)."""
        now_cheap = is_cheap_now(self._read_price_input())
        rollover_action = decide_daily_rollover(
            DailyRolloverInput(was_suppressing=self._suppressing, low_solar_today=self._today_low_solar)
        )
        await self._apply_rollover_action(rollover_action)
        edge_action = decide_window_edge(
            WindowEdgeInput(
                entering=bool(now_cheap),
                leaving=False,
                low_solar_today=self._today_low_solar,
                car_connected=self._read_car_connected(),
            )
        )
        await self._apply_window_action(edge_action)
        self._was_cheap = now_cheap

    def _refresh_status(self) -> None:
        self.status_text = status_text(
            enabled=self.enabled,
            forecast_kwh=self._today_forecast_kwh,
            threshold_kwh=self.forecast_threshold,
            low_solar_today=self._today_low_solar,
            cheap_now=bool(self._was_cheap),
            forced_active=self._forced_active,
            car_connected=self._read_car_connected(),
        )
        async_dispatcher_send(self.hass, self.signal)

    # --- entity-facing API ----------------------------------------------

    async def async_set_forecast_threshold(self, value: float) -> None:
        self.forecast_threshold = value
        self._refresh_status()

    async def async_set_price_threshold(self, value: float) -> None:
        self.price_threshold = value
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
            # is flipped off, since _apply_window_action/_apply_rollover_
            # action only perform their actual go-e/switch calls while
            # `self.enabled` is still true.
            if self._forced_active:
                await self._apply_window_action(ACTION_STOP_FORCED_CHARGE)
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
