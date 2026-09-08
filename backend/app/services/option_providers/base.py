"""Provider interface for option-chain data.

The abstraction exists for the reason you already flagged: Yahoo publishes
options for US listings only. When a European source arrives it implements this
interface and is selected by ``OPTION_PROVIDER``; nothing above this layer
changes. Same shape as fibenchi's ``PriceProvider``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime

from app.domain.occ import ContractRef


@dataclass(frozen=True, slots=True)
class RawOptionQuote:
    """One contract's quote as the provider reported it, already normalised.

    ``bid``/``ask``/``last`` are ``None`` rather than 0.0 when absent — the
    normalisation happens in the provider so that no consumer ever sees a
    provider's "0.0 means no quote" convention. That single mapping is the
    difference between a clean IV surface and one with a floor artefact.
    """

    ref: ContractRef
    bid: float | None
    ask: float | None
    last: float | None
    last_trade_at: datetime | None
    volume: int
    open_interest: int | None
    iv_provider: float | None
    contract_size: str
    currency: str


@dataclass(frozen=True, slots=True)
class RawChain:
    """A complete chain snapshot for one underlying, at one instant.

    Spot travels with the chain rather than being fetched separately, because
    the provider returns both in a single response. That is not a convenience:
    greeks computed from a spot sampled at a different moment than the option
    quotes are wrong by however far the stock moved in between, and near the
    open that is not a small number.
    """

    symbol: str
    name: str
    currency: str
    spot: float | None
    bid: float | None
    ask: float | None
    previous_close: float | None
    market_state: str | None
    session_date: date | None
    fetched_at: datetime
    expirations: list[date]
    quotes: list[RawOptionQuote]

    def __len__(self) -> int:
        return len(self.quotes)


class OptionProvider(ABC):
    """Source of option chains."""

    @abstractmethod
    async def fetch_chain(self, symbol: str) -> RawChain:
        """Fetch the full option chain for one underlying.

        Raises :class:`ChainUnavailable` when the symbol has no options (every
        non-US listing, and plenty of US small caps).
        """

    @abstractmethod
    async def fetch_chains(self, symbols: list[str]) -> dict[str, RawChain]:
        """Fetch chains for several underlyings.

        Symbols that fail are omitted rather than raising, so one bad ticker
        cannot abort a polling cycle for the rest of the portfolio.
        """

    @abstractmethod
    async def validate(self, symbol: str) -> tuple[bool, str]:
        """Whether ``symbol`` is trackable, and its display name.

        Used when adding an underlying, so a user gets an immediate, specific
        rejection ("no listed options") instead of a ticker that silently
        collects nothing.
        """


class ChainUnavailable(Exception):
    """The provider has no option chain for this symbol."""
