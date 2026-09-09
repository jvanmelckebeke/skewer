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

from app.domain.contracts import ContractRef
from app.domain.venue import Venue


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
    contract_size: int
    currency: str

    # The exchange's own official mark, where the venue publishes one.
    #
    # This exists because Eurex's free feed has no order book at all: it
    # publishes one settlement price per contract per session and nothing
    # else. That is emphatically *not* the same thing as Yahoo's zero bid.
    # A zero bid is the absence of a quote, and the invariant that it must
    # never become a mark is what keeps the IV surface clean. A settlement
    # price is the opposite: it is the exchange's considered valuation,
    # struck against the closing auction, and it is the most authoritative
    # number of the day.
    #
    # So a settlement is allowed to become a mark when there is no two-sided
    # quote to prefer, and ``chain_sync`` does exactly that. It stays a
    # separate field rather than being written into ``bid``/``ask`` so that
    # nothing downstream can mistake it for a book that was never there.
    settlement: float | None = None


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
    venue: Venue
    spot: float | None
    bid: float | None
    ask: float | None
    previous_close: float | None
    market_state: str | None
    session_date: date | None
    fetched_at: datetime
    expirations: list[date]
    quotes: list[RawOptionQuote]
    # What ``spot`` is denominated in, when the provider knows. ``currency``
    # above is the *option's*, and on a composed feed the two can differ: the
    # Eurex product COPA settles in USD while its spot comes from Yahoo's
    # Milan listing in EUR. None means the provider cannot say, which is
    # treated as agreement rather than as a mismatch.
    spot_currency: str | None = None

    def __len__(self) -> int:
        return len(self.quotes)


class OptionProvider(ABC):
    """Source of option chains.

    Implementations are selected per *underlying* rather than per process.
    Skewer polls Yahoo for US names and Eurex for German ones in the same
    collection cycle, so "which provider" is a property of the row being
    polled — see ``option_providers.for_venue``.
    """

    #: The market this provider speaks for. Used to route an underlying to it.
    venue: Venue

    #: Whether this provider reports a live order book. False means the feed
    #: is end-of-day settlement only, which changes what a poll can produce:
    #: there is no intraday shape to sample, so the cadence grid has nothing
    #: to select between and one session yields one row per contract.
    intraday: bool = True

    @abstractmethod
    async def fetch_chain(self, symbol: str, product: str | None = None) -> RawChain:
        """Fetch the full option chain for one underlying.

        ``product`` is the venue's own code for the option series, from
        ``underlyings.venue_product``. It is not derivable from the ticker in
        general — Eurex lists ArcelorMittal's options under ``ISPA`` — so a
        provider that has its own symbology takes it here and falls back to
        the ticker when it is None. Yahoo ignores it: on OPRA the ticker *is*
        the product.

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
    async def validate(self, symbol: str, product: str | None = None) -> tuple[bool, str]:
        """Whether ``symbol`` is trackable, and its display name.

        Used when adding an underlying, so a user gets an immediate, specific
        rejection ("no listed options") instead of a ticker that silently
        collects nothing.

        ``product`` has to be honoured here and not only in ``fetch_chain``:
        validation is what decides whether the row is created, so a provider
        that ignores it rejects every underlying whose venue code differs from
        its ticker before the code it was given is ever stored.
        """


class ChainUnavailable(Exception):
    """The provider has no option chain for this symbol."""
