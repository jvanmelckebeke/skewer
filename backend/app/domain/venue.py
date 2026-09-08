"""Where a contract trades, and what that implies about it.

Skewer began US-only, and three facts about the US market were baked in as
constants rather than as data: that a contract's identity is an OCC symbol,
that it expires at 16:00 America/New_York, and that it is quoted in USD. None
of those survive contact with Eurex, where BMW options settle in EUR against a
17:30 Europe/Berlin close and carry no OCC symbol at all.

This module is where those facts move from "hardcoded" to "a property of the
venue". A :class:`VenueSpec` is looked up once per contract and answers the
questions the pricing layer used to answer by assumption.

Adding a third venue (Euronext, say) means one entry in ``VENUES`` and a
symbology in ``domain/symbology.py``. It must not mean touching pricing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from enum import Enum
from zoneinfo import ZoneInfo


class Venue(str, Enum):
    """The market a contract is listed on.

    Stored on ``option_contracts`` and used to pick the symbology, the expiry
    instant and the provider. The value is what lands in PostgreSQL, so it is
    lowercase to match the convention ``Right`` already set.
    """

    OCC = "occ"
    """US listed equity options, cleared by the OCC. Yahoo's universe."""

    EUREX = "eurex"
    """Eurex single-stock and index options. Deutsche Boerse's universe."""


class ExerciseStyle(str, Enum):
    """When the holder may exercise.

    Recorded rather than assumed, because Eurex publishes it per contract and
    lists both styles on the same underlying (BMW has an American class and a
    European one, same expiry, different trading class).

    A caveat that matters more than the field does: skewer prices every
    contract with Black-Scholes-Merton, which is a European model. That was
    already an approximation before Eurex arrived, because US listed equity
    options are American-style too and always have been. The approximation is
    good for calls on low-yield names and worst for deep in-the-money puts,
    where the early-exercise premium is real money.

    So this field does not currently change how anything is priced. It records
    what the contract actually is, so that the day a Bjerksund-Stensland or
    binomial path lands, the data needed to dispatch on it is already stored
    rather than needing a backfill.
    """

    EUROPEAN = "european"
    AMERICAN = "american"


@dataclass(frozen=True, slots=True)
class VenueSpec:
    """The venue-dependent facts pricing needs.

    Frozen for the same reason ``ContractRef`` is: it gets captured once and
    carried through loops that commit and roll back.
    """

    venue: Venue
    label: str

    # The expiry *instant*, not the expiry date. At 0DTE the difference between
    # "expires at midnight" and "expires at the close" is the option's entire
    # remaining life, so this is a correctness input, not presentation.
    expiry_tz: ZoneInfo
    expiry_time: time

    # The exchange-local trading session, used to decide whether a poll is
    # happening while the book is live.
    session_open: time
    session_close: time

    currency: str
    default_exercise_style: ExerciseStyle
    default_contract_size: int

    def expires_at(self, expiration: date) -> datetime:
        """The timezone-aware expiry instant for a contract on this venue."""
        return datetime.combine(expiration, self.expiry_time, tzinfo=self.expiry_tz)


VENUES: dict[Venue, VenueSpec] = {
    Venue.OCC: VenueSpec(
        venue=Venue.OCC,
        label="US listed options (OCC)",
        expiry_tz=ZoneInfo("America/New_York"),
        # Most listed equity options have been PM-settled since 2015. The
        # handful of AM-settled index options are not tracked yet.
        expiry_time=time(16, 0),
        session_open=time(9, 30),
        session_close=time(16, 0),
        currency="USD",
        # US listed equity options are American-style. Stating it explicitly
        # rather than leaving it implied is the point of this field.
        default_exercise_style=ExerciseStyle.AMERICAN,
        default_contract_size=100,
    ),
    Venue.EUREX: VenueSpec(
        venue=Venue.EUREX,
        label="Eurex",
        expiry_tz=ZoneInfo("Europe/Berlin"),
        # Eurex equity options stop trading at 17:30 CET on the last trading
        # day. The settlement that matters is struck against the Xetra close
        # auction, which is inside that window.
        expiry_time=time(17, 30),
        session_open=time(9, 0),
        session_close=time(17, 30),
        currency="EUR",
        # Overridden per contract from the feed, which publishes ExerciseStyle
        # directly. This is only the fallback for a row that omits it.
        default_exercise_style=ExerciseStyle.AMERICAN,
        default_contract_size=100,
    ),
}


def spec_for(venue: Venue | str) -> VenueSpec:
    """Look up a venue's spec, accepting either the enum or its stored value."""
    key = Venue(venue) if not isinstance(venue, Venue) else venue
    try:
        return VENUES[key]
    except KeyError:  # pragma: no cover - unreachable while Venue is closed
        raise ValueError(f"No VenueSpec registered for {key!r}") from None
