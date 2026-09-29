"""The ibkr-svc implementation of :class:`~app.services.ibkr.base.IbkrGateway`.

skewer never talks to IB Gateway itself. ibkr-svc owns the one API session,
the market-data line budget and the pacing, and fibenchi uses it too; its
OpenAPI document lives in the ibkr-gateway repo at ``svc/openapi.json``. The
models in ``base.py`` type the fields skewer reads and ignore the rest.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.config import settings
from app.services.ibkr.base import ContractInfo
from app.services.option_providers.base import ProviderUnavailable


class HttpIbkrGateway:
    """ibkr-svc over HTTP. Any transport or 5xx failure is ProviderUnavailable."""

    def __init__(self, base_url: str | None = None, timeout: float | None = None) -> None:
        self._base_url = (base_url or settings.ibkr_svc_url).rstrip("/")
        self._timeout = timeout or settings.ibkr_timeout_seconds

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

    async def resolve_isin(self, isin: str) -> list[ContractInfo]:
        """``POST /contracts/resolve`` by ISIN."""
        data = await self._request(
            "POST", "/contracts/resolve", json={"secIdType": "ISIN", "secId": isin}
        )
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
