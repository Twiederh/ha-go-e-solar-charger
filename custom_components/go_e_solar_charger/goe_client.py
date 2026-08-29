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

    async def push_pv_values(self, values: dict) -> None:
        """values: e.g. {"pPv": 3200.5, "pGrid": -450.0, "pAkku": -1200.0}"""
        url = f"http://{self._host}/api/set"
        ids = json.dumps(values)
        async with self._session.get(
            url, params={"ids": ids}, headers=self._headers(), timeout=TIMEOUT
        ) as response:
            response.raise_for_status()
            await response.json(content_type=None)
