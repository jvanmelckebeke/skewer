"""HTTP client for the shared ibkr-svc gateway.

skewer never talks to IB Gateway itself. ibkr-svc owns the one API session,
the market-data line budget and the pacing, and fibenchi uses it too; its
OpenAPI document lives in the ibkr-gateway repo at ``svc/openapi.json``. The
models here type the fields skewer reads and ignore the rest.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx
from pydantic import BaseModel

from app.config import settings
from app.services.option_providers.base import ProviderUnavailable


class ContractInfo(BaseModel):
    """One resolved IBKR contract. Eurex options carry no ISIN (``isin`` is null)."""

    conId: int
    secType: str = ""
    symbol: str = ""
    tradingClass: str = ""
    lastTradeDateOrContractMonth: str = ""
    strike: float = 0.0
    right: str = ""
    multiplier: str = ""
    currency: str = ""
    exchange: str = ""
    localSymbol: str = ""
    underConId: int = 0
    isin: str | None = None

    @property
    def expiry(self) -> date | None:
        text = self.lastTradeDateOrContractMonth[:8]
        try:
            return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
        except (ValueError, IndexError):
            return None


class IbkrClient:
    """Async client for ibkr-svc. Any transport or 5xx failure is ProviderUnavailable."""

    def __init__(self, base_url: str | None = None, timeout: float | None = None) -> None:
        self._base_url = (base_url or settings.ibkr_svc_url).rstrip("/")
        self._timeout = timeout or settings.ibkr_timeout_seconds

    @property
    def configured(self) -> bool:
        return bool(self._base_url)

    async def _request(self, method: str, path: str, **kwargs) -> Any:
        try:
            async with httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout) as client:
                response = await client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"ibkr-svc unreachable: {exc}") from exc
        if response.status_code >= 500:
            raise ProviderUnavailable(f"ibkr-svc {response.status_code}: {response.text[:200]}")
        response.raise_for_status()
        return response.json()

    async def resolve(self, **spec) -> list[ContractInfo]:
        """``POST /contracts/resolve`` with a ContractSpec (secType, symbol, exchange, ...)."""
        data = await self._request("POST", "/contracts/resolve", json=spec)
        return [ContractInfo.model_validate(row) for row in data]

    async def option_contracts(
        self, under_con_id: int, exchange: str = "EUREX", max_dte: int | None = None
    ) -> list[ContractInfo]:
        """``GET /options/{underConId}/contracts``, cached by the service per exchange day."""
        params: dict[str, object] = {"exchange": exchange}
        if max_dte is not None:
            params["max_dte"] = max_dte
        data = await self._request("GET", f"/options/{under_con_id}/contracts", params=params)
        return [ContractInfo.model_validate(row) for row in data]
