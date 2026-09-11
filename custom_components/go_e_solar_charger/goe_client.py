"""Minimal async client for the parts of the go-eCharger local API v2 this
integration needs: forcing charging off/on again, and feeding PV-surplus
values into the charger's own charging logic.

See https://github.com/goecharger/go-eCharger-API-v2/blob/main/http-en.md -
values are set via GET query parameters, e.g. `/api/set?frc=1`. The
"ids" key is go-e's own batch mechanism for pPv/pGrid/pAkku (external
PV-surplus-charging input) - one GET request setting all three at once.
"""
import json
import logging
from typing import Optional

import aiohttp

from .const import FRC_NEUTRAL, FRC_OFF, FRC_ON

_LOGGER = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=10)


class GoEClient:
    def __init__(self, session: aiohttp.ClientSession, host: str, api_key: str = ""):
        self._session = session
        self._host = host
        self._api_key = api_key

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}

    async def _set(self, key: str, value: int) -> None:
        url = f"http://{self._host}/api/set"
        async with self._session.get(
            url, params={key: value}, headers=self._headers(), timeout=TIMEOUT
        ) as response:
            response.raise_for_status()
            body = await response.json(content_type=None)
            if body.get(key) is not True:
                raise RuntimeError(f"go-e lehnte {key}={value} ab: {body}")

    async def stop_charging(self) -> None:
        _LOGGER.info("Stoppe Ladevorgang am go-e %s (frc=Off)", self._host)
        await self._set("frc", FRC_OFF)

    async def release(self) -> None:
        _LOGGER.info("Gebe go-e %s wieder frei (frc=Neutral)", self._host)
        await self._set("frc", FRC_NEUTRAL)

    async def force_charging_on(self) -> None:
        """Force charging on regardless of PV surplus / amp settings - used
        for grid-cheap-price charging on days with a poor solar forecast."""
        _LOGGER.info("Erzwinge Laden am go-e %s (frc=On)", self._host)
        await self._set("frc", FRC_ON)

    async def set_amp(self, value: int) -> None:
        """Requested charging current in Amps - used by the direct-control
        feature (pv_direct_controller.py) instead of the ids/pPv-push
        mechanism above."""
        _LOGGER.info("Setze Ladestrom am go-e %s auf %s A", self._host, value)
        await self._set("amp", value)

    async def set_phase_mode(self, value: int) -> None:
        """Phase switch mode ("psm") - see const.py's PSM_* constants and
        their confidence caveat. Used only by the direct-control feature."""
        _LOGGER.info("Setze Phasenmodus am go-e %s auf %s", self._host, value)
        await self._set("psm", value)

    async def push_pv_values(self, values: dict) -> None:
        """values: e.g. {"pPv": 3200.5, "pGrid": -450.0, "pAkku": -1200.0}"""
        url = f"http://{self._host}/api/set"
        ids = json.dumps(values)
        async with self._session.get(
            url, params={"ids": ids}, headers=self._headers(), timeout=TIMEOUT
        ) as response:
            response.raise_for_status()
            await response.json(content_type=None)

    async def get_car_state(self) -> Optional[int]:
        """Reads go-e's "car" (carState) status field - see const.py's
        CAR_STATE_* constants. Used only to sanity-check the direct-control
        feature's own "is the car actually drawing what I last requested"
        assumption (see pv_direct_logic.py's module docstring): unlike
        psm/nrg, this field is consistently documented across independent
        go-e API sources, so it's read despite this integration otherwise
        avoiding go-e's status API. Returns None if the field is missing or
        unparsable, rather than raising - callers treat that as "unknown",
        not "confirmed not charging"."""
        url = f"http://{self._host}/api/status"
        async with self._session.get(
            url, headers=self._headers(), timeout=TIMEOUT
        ) as response:
            response.raise_for_status()
            body = await response.json(content_type=None)
        try:
            return int(body.get("car"))
        except (TypeError, ValueError):
            return None

    async def get_total_power_w(self) -> Optional[float]:
        """Live total charging power from go-e's "nrg" status array.
        Reported in practice: this feature's "assumed car draw" guess
        (last-commanded amp * phase * 230 V, see pv_direct_logic.py's
        module docstring) can badly overstate the surplus not just when
        the car has stopped entirely (see get_car_state() above), but
        also when it's charging at *less* than commanded - e.g. its own
        charge curve tapering as the battery nears full - which a plain
        charging/not-charging check can't catch at all.

        go-e's "nrg" field itself isn't documented reliably enough to
        trust blindly (same caveat as psm - see this module's docstring),
        so index 11 is used only because it was cross-checked against a
        real device: it exactly equalled the sum of the three per-phase
        power readings at indices 7-9 in every status snapshot seen so
        far, which is a much stronger basis than trusting the field name/
        position from documentation alone. Returns None if "nrg" is
        missing, too short, or unparsable, rather than raising - callers
        treat that as "unknown" and fall back to the amp/phase guess.
        """
        url = f"http://{self._host}/api/status"
        async with self._session.get(
            url, headers=self._headers(), timeout=TIMEOUT
        ) as response:
            response.raise_for_status()
            body = await response.json(content_type=None)
        try:
            return float(body.get("nrg")[11])
        except (TypeError, ValueError, IndexError):
            return None
