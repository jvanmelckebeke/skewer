"""The IBKR interface skewer depends on, and the types it speaks.

Same split as ``option_providers/base.py``: this module says *what* skewer
needs from IBKR, ``client.py`` is the one implementation (HTTP to the shared
ibkr-svc gateway), and the registry in ``__init__.py`` hands it out. Callers
take an :class:`IbkrGateway` as a parameter, so tests pass a fake.

A ``Protocol`` rather than an ABC, like ``SpotSource``: a fake implements only
the calls its test makes, and nothing inherits behaviour from here.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol

from pydantic import BaseModel


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


class IbkrGateway(Protocol):
    """What skewer asks of IBKR. Any failure to answer is ``ProviderUnavailable``."""

    async def resolve_isin(self, isin: str) -> list[ContractInfo]:
        """Every listing of the instrument with this ISIN, one row per exchange."""
        ...

    async def option_contracts(
        self, under_con_id: int, exchange: str = "EUREX", max_dte: int | None = None
    ) -> list[ContractInfo]:
        """The option chain on ``exchange`` for the underlying with this conId."""
        ...
