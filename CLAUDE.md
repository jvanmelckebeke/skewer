# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What skewer is

Option-chain collection and analytics. Sibling project to **fibenchi**
(`../fibenchi`), which tracks stock/ETF price history. Skewer deliberately
reuses fibenchi's architecture — layered backend, repository pattern, registry
pattern for background jobs and metrics — so knowledge transfers between them.

The division of labour: **fibenchi owns the underlying's price history; skewer
owns the option surface.** Skewer stores only the spot it needs for pricing
(`underlying_quotes`), not OHLCV. Don't rebuild fibenchi's charting/indicator
stack here.

## Development environment

```bash
docker compose up -d              # db (TimescaleDB) + backend + frontend
docker compose exec backend alembic upgrade head
docker compose logs -f backend
```

| Service  | Internal | Exposed | Notes |
|----------|----------|---------|-------|
| db       | 5432     | 5433    | TimescaleDB (PG16) — port avoids fibenchi's 5432 |
| backend  | 8000     | 18100   | FastAPI + uvicorn --reload — avoids fibenchi's 18000 |
| frontend | 80       | —       | nginx; reached at http://skewer.desktop.lan via Traefik |

Both projects can run simultaneously; the ports are chosen for that.

The frontend publishes no host port — the host's Traefik routes
`skewer.desktop.lan` to it. Its nginx also proxies `/api` (and `/docs`) to the
backend, so the SPA and the API share one origin and there is no CORS config
anywhere. For HMR instead of a rebuild:

```bash
docker compose -f docker-compose.yaml -f docker-compose.dev.yml up frontend
```

## Commands

```bash
docker compose exec backend pytest
docker compose exec backend pytest tests/test_pricing.py -k iv
docker compose exec backend ruff check app/ tests/

# Collection-path tests need PostgreSQL (ON CONFLICT upserts; SQLite can't):
SKEWER_TEST_DATABASE_URL=postgresql+asyncpg://skewer:skewer@db:5432/skewer_test pytest
```

Tests default to SQLite in-memory and skip the ~20 collection tests without
that env var. Both paths must pass.

`docker-compose.yaml` builds the backend's `dev` stage, which is where pytest,
ruff and aiosqlite live. A build with no target stops at `runtime` and carries
none of them, so deploying does not ship the test tooling.

## Architecture

FastAPI (async) → SQLAlchemy async → PostgreSQL/TimescaleDB via asyncpg.
APScheduler runs the collection jobs.

### The star schema — read this before touching models

```
underlyings ──< option_contracts ──< option_quotes      (hypertable, the fact table)
     │                   │
     │                   └────────< option_daily_stats  (one row per contract per session)
     └────────< underlying_quotes                       (hypertable, spot series)
```

`option_contracts` is a **dimension table**. Strike, expiration and right are
immutable — they're encoded in the OCC symbol itself — so they live there once
and a quote row carries only `contract_id`. This keeps the fact row ~40 bytes.
**Never denormalise contract metadata onto `option_quotes`.** That decision is
the whole reason the storage math works.

`option_quotes` and `underlying_quotes` are Timescale hypertables with a
`(contract_id, ts)` / `(underlying_id, ts)` natural PK. **No surrogate `id`** —
Timescale requires the partition column in every unique index, and fibenchi
already learned that `ON CONFLICT` burns a sequence value per *attempted* row
(its intraday table exhausted an int32 sequence).

Open interest lives **only** in `option_daily_stats`, never on a tick — it
changes once a day (published overnight by the OCC), so storing it per tick
would write the same integer ~78 times a session.

### Backend layout

- `domain/contracts.py` — `ContractRef`, the venue-agnostic contract identity
  (frozen, like fibenchi's `AssetRef`, and for the same reason: it survives the
  rollbacks a batch loop performs, where an ORM instance would expire and raise
  `MissingGreenlet`). Re-exported from `domain/occ.py`, so the old import path
  still works.
- `domain/venue.py` — `Venue`, `ExerciseStyle`, and the per-venue facts that used
  to be hardcoded US constants: the expiry instant, the session window, the
  currency. Adding a market means an entry here plus a symbology.
- `domain/occ.py` — **the only place OCC symbols are parsed or built.** Parsing
  also *validates*: provider strike/expiry are cross-checked against the symbol
  at ingest. **No other venue gives this for free** — a Eurex ISIN encodes
  nothing, so its terms are believed rather than checked.
- `domain/eurex.py` — Eurex identity. Contracts are keyed `EUX:` + ISIN. The
  key is called `symbol` everywhere: the column, the `ContractRef`, the API
  response and `src/types.ts`. It is an OCC symbol only on the OCC venue.
- `domain/euronext.py` — Euronext identity, keyed `XNT:<EXCODE>:<identifier>`.
  The prefixes are what make the three namespaces provably non-colliding in the
  one unique index they share.
- `domain/cadence.py` — the capture policy. Tenor buckets, wall-clock slot
  alignment, `should_persist`. Change cadence here and nowhere else.
- `models/` — SQLAlchemy declarative with `Mapped[]`.
- `repositories/` — all DB access. Upserts are chunked (`UPSERT_CHUNK_ROWS`)
  against PostgreSQL's 32767 bind-parameter ceiling; a full chain is ~3.3k rows
  and SPY's is several times that.
- `services/option_providers/` — `OptionProvider` ABC plus Yahoo (OCC), Eurex
  and Euronext impls, resolved **per underlying by venue**, not by a global
  setting. NVDA and BMW.DE are polled in the same cycle from different feeds.
  Every Yahoo request goes through `YahooOptionProvider._call`: 1 s spacing,
  one reused session, and a circuit breaker on "Invalid Crumb". The throttle
  in `yahoo_rate_limit.py` is a copy of fibenchi's, so fix both together.
- `services/ibkr/` — `IbkrGateway` (a Protocol in `base.py`) is what skewer
  asks of IBKR; `HttpIbkrGateway` is the one implementation, against the shared
  ibkr-svc (its OpenAPI document is `svc/openapi.json` in the ibkr-gateway
  repo), and `get_gateway()` hands it out, None when unconfigured. IBKR is a
  source, not a venue: `venues.py` holds the per-venue IBKR data (option
  exchange, trading-class suffix, where the underlying ISIN comes from) and
  `IBKR_VENUES` enables entries, only `eurex` by default. The daily
  `ibkr_catalog` job gives each live contract on an enabled venue its
  `ibkr_con_id`.
  IBKR has no ISIN for Eurex options, so the link is by terms: (tradingClass
  minus any `.N` suffix = Eurex product, expiry, strike to 4 dp, right,
  multiplier), against rows in the latest Deutsche Boerse catalog only. Zero
  or several candidates write nothing. The job is off without `IBKR_SVC_URL`.
- `services/chain_sync.py` — fetch → register contracts → cadence-select →
  solve IV → persist. `persist_chain()` is split from `sync_underlying()` so the
  pipeline is testable without a network.
- `services/compute/` — pure, I/O-free, vectorised over numpy arrays:
  - `bs.py` — Black-Scholes-Merton pricing and greeks. `phi` (+1 call / -1 put)
    collapses call/put into one formula set.
  - `iv.py` — the IV solver. **Read its docstring before changing it.** It
    takes an `american` mask and switches both the price function and the
    no-arbitrage bounds together; switching one without the other rejects
    good deep-ITM quotes.
  - `american.py` — Bjerksund-Stensland 2002, closed-form so it vectorises.
    Dispatched on `option_contracts.exercise_style`. A call on a non-payer
    returns the European price exactly, which is most of an OCC chain. Greeks
    are **not** American: they are European sensitivities at the American
    implied volatility, because a finite-differenced greek costs ten more
    price evaluations per chain read.
  - `chain.py` — `enrich()` builds a `ChainSnapshot` from raw columns.
  - `analytics.py` — `METRIC_REGISTRY`, skewer's answer to fibenchi's
    `INDICATOR_REGISTRY`. Add a metric with the `@chain_metric` decorator.
- `background_tasks/` — the registry pattern, ported from fibenchi verbatim.
  Jobs self-register via `@background_task(id=..., trigger=...)`; the lifespan
  schedules `all_tasks()`. **Adding a job never touches `main.py`.** A trigger
  factory returning `None` disables just that job.
- `services/market_calendar.py` — per-venue session gate. Each `VenueSpec`
  names its exchange calendar (`XNYS`, `XEUR`, `XAMS`), which sets the session
  days; the spec's own hours clip it (XEUR is 08:00-22:00 for futures, Eurex
  options trade 09:00-17:30). `VenueSpec.feed_delay` shifts the window for a
  delayed feed. `sync_all(only_due=True)` skips underlyings whose venue is shut.
  Fail-safe: if the calendar can't load, fall back to weekday heuristics and
  keep collecting.

### Units — mixing these is the classic source of wrong-looking greeks

- `t` is **years**, ACT/365, to the **16:00 ET** expiry instant (not midnight —
  at 0DTE that difference is the option's whole remaining life).
- `sigma`, `r`, `q` are annualised decimals (0.35 = 35%).
- `bs.greeks()` returns **vega per 1.00 of vol** and **theta per year**. The API
  serialises `per_point(vega)` and `per_day(theta)`. Convert at the edge only.
- Delta is per share, not per 100-share contract.

## Key invariants

- **A zero bid is not a price.** Yahoo returns `bid: 0.0`/`ask: 0.0` for most
  contracts outside regular hours (2,954 of NVDA's 3,286 in pre-market). The
  provider maps zero → `None`; `mid_price()` requires a genuine two-sided quote.
  Never let a 0.0 become a mark.
- **IV is NULL unless it is identifiable.** Bisection always converges — that's
  the trap. When vega ≈ 0 every sigma reproduces the price, so a "converged"
  answer is the bracket edge. `MIN_VEGA` gates on the price actually being
  sensitive to sigma at quote resolution. This is the guard that stops skewer
  reproducing the provider's `1e-05` artefact. There is a regression test;
  keep it.
- **No IV means no greeks.** Unpriced contracts return `None`, never `0.0` —
  a zero delta plots and lies. `enrich()` masks them out so a single NaN can't
  poison an aggregate sum.
- **Timestamps are snapped to the grid.** Every contract in one poll shares a
  `ts`, making a stored snapshot a true simultaneous cross-section. Reads use
  exact `ts` equality, not a window. Never interpolate a contract into a slot it
  wasn't sampled in.
- **Contracts are catalogued even when not quoted.** The dimension table is the
  record of what exists, independent of sampling. Expired contracts are kept —
  their history is the historical record.
- **Group membership is the collection switch.** An underlying in no group isn't
  polled but keeps its data. `is_active=false` does the same. `DELETE` is a real
  cascade — unlike fibenchi's soft delete, since nothing else references a
  contract.

## Gotchas already hit

- `Enum(Right, ...)` needs `values_callable=lambda e: [m.value for m in e]`.
  SQLAlchemy stores the enum *name* (`"CALL"`) by default, but the migration
  creates the type from *values* (`'call'`). Without it: `InvalidTextRepresentationError`.
- Don't pre-create the `option_right` type in a migration *and* let
  `op.create_table` create it — that's a `DuplicateObjectError`.
- `exchange_calendars.previous_session()` raises `NotSessionError` unless its
  argument already *is* a session. Use `date_to_session(day, direction="previous")`.
- The test `engine` fixture must `drop_all` before `create_all`. In-memory
  SQLite is fresh per test; a PostgreSQL test DB is not.
- `RawChain`/`ContractRef` are `slots=True` dataclasses — no `__dict__`. Use
  `dataclasses.replace()`.
- **Don't use `yahooquery`'s `option_chain` DataFrame property.** It calls
  `fillna(0)`, erasing the null/zero distinction, and drops the `quote` block
  that carries spot and market state. Use `_get_data("options", {"getAllData": True})`.

## Adding things

**A chain metric:** decorate a function in `services/compute/analytics.py` with
`@chain_metric(id, label, unit, description)`. It takes a `ChainSnapshot`,
returns `float | None`. Return `None` rather than extrapolating — see
`_interp_at`, which refuses to read ATM vol off a chain that doesn't bracket the
money. `GET /api/chains/metrics` picks it up automatically.

**A chain metric, in the UI:** nothing. `GET /api/chains/metrics` drives the
stat tiles, so a registered metric appears with its label, unit and description
without a frontend change.

**A read shape:** there are three, and they answer different questions.
`chain_view` is one instant, `contract_series` is one contract over time, and
`session_profile` is every strike in one expiration over one session (quantiles
per contract, computed in SQL). Adding a fourth means a repository method, a
service function and a schema — the router stays thin.

**A background job:** write it in `background_tasks/jobs.py` with
`@background_task(...)`. Own your session, swallow your own exceptions.

**A data provider:** implement `OptionProvider` in `services/option_providers/`,
give it a `venue` and an `intraday` flag, and register it in that package's
`_PROVIDERS`. Routing is by `underlyings.venue`, so nothing above the registry
changes. Normalise "no quote" to `None` in the provider — that's the layer's job.
`validate()` returns `(False, "")` only when the venue answered "no options";
if it could not answer, raise `ProviderUnavailable` so adding the ticker says
"try again later". A venue with no spot of its own takes a `SpotSource` and
reads it through `spot_from()`, which carries the spot's own currency into
`RawChain.spot_currency`. `fetch_chains` has a sequential default; override it
only if the feed can fetch several symbols in one request.

**A venue:** add a `VenueSpec` to `domain/venue.py` (expiry instant, session,
currency), a symbology module alongside `domain/occ.py`, a provider, and a value
to the `option_venue` enum via a migration.

## Frontend

React 19 + TypeScript + Vite, Tailwind v4 and shadcn/ui, TanStack Query for
server state, Recharts for the plots, React Router for five pages
(overview, chain, contract, groups, collector).

- `src/types.ts` mirrors `backend/app/schemas/`. **Nullable there means nullable
  here.** The formatters in `lib/format.ts` render a missing number as `–`, never
  `0` — the same reason the backend returns NULL rather than a number: a zero
  delta plots and lies.
- `lib/api.ts` is the only place that calls `fetch`. Relative `/api` paths, no
  base URL, no CORS.
- `hooks/queries.ts` holds every query key and mutation. Invalidations live with
  the mutation that needs them.
- Chart colours are CSS custom properties in `index.css` (`--viz-1..3`), one
  block per theme. They are the first three slots of a colour-blind-validated
  categorical order, checked against this app's real surfaces — **re-run the
  validator before adding a fourth**, and never assign a hue by rank. Calls are
  always slot 1 and puts always slot 2 so a filter never repaints them.
- Every chart ships a table view (`ChartCard`), which is what keeps the light
  theme's low-contrast series legible.
- Price and volatility never share a plot. Two scales on one y-axis invents a
  correlation the data does not contain; the contract page uses two charts.
- `TooltipTrigger asChild` writes `data-state` onto its child. Never put it
  directly on a Radix control that styles itself from that attribute (the
  `Switch` did, and rendered invisible) — wrap the control in a span instead.

## The session profile

`GET /api/chains/{symbol}/session` returns per-strike IV quantiles across one
stored session. Both query parameters are optional, and the response carries
back the lists that populate their pickers, so the client needs one round trip.

- **The expiration list comes from the session, never from the live chain.** A
  session three weeks old holds expiries that have since expired off the chain,
  and those are the ones with the densest history.
- **Sessions carry slot counts** because a one-slot session is real but cannot
  describe a range — its quantiles collapse onto the single observation. The
  default lands on the most recent session holding more than one slot, and on
  the nearest expiry *after* that session, since a 0DTE contract's IV goes
  vertical into the close.
- **The chart shows out-of-the-money strikes by default.** This is how a smile
  is built, not a convenience. Measured on NVDA's 24 Aug expiry over the 21 Aug
  session, OTM calls implied 24-43% and OTM puts 24-60%; the ITM halves of the
  same chain reached 274% and 120%, because an ITM price is nearly all
  intrinsic and the mid barely moves with vol. Spread width does *not* separate
  the good from the bad here — a tightly quoted deep-ITM contract still implies
  nonsense — so moneyness is the filter, not liquidity.
- PostgreSQL-only: ordered-set aggregates and `timezone()`. The tests skip
  without `SKEWER_TEST_DATABASE_URL`, same as the collection ones.

## Eurex (the second venue)

Deutsche Boerse's public GraphQL API at
`https://api.developer.deutsche-boerse.com/eurex-prod-graphql/graphql` (note the
trailing `/graphql`; Eurex's own docs give a URL that 404s) serves contract
terms, `ExerciseStyle`, `VersionNr` and daily `SettlementPrices`, free and
anonymous. It is CORS-open, so Apollo Sandbox works against it directly.

- **Intraday comes from IBKR, when configured.** With `IBKR_SVC_URL` set and
  an underlying's `ibkr_con_id` resolved, its intraday polls go to
  `IbkrOptionProvider` (free delayed data, `feed_delay` 15 min). The cadence
  picks the contracts *before* the fetch, staggered by expiry, because each is
  one request on a shared line budget. The poll is stamped `floor(start - 15
  min)`; a quote is kept only if its market-data type is `delayed` and it
  arrived inside that slot's window. Deutsche Boerse stays the catalog and the
  EOD settlement, and an IBKR poll never touches `last_seen_at`.
- **The free feed has no order book.** No bid, no ask, no intraday anything. `bid`/`ask`
  are NULL because the field does not exist, not because the book was empty.
  `intraday = False` makes every Eurex poll a full one, since cadence selection
  has nothing to choose between.
- **A settlement price is allowed to become a mark.** This does not weaken the
  zero-bid rule, it is its mirror image: Yahoo's 0.0 is the *absence* of a
  quote, while a settlement is the exchange's own valuation struck against the
  closing auction.
- **The option's currency and the spot's are checked, not assumed.** Two feeds
  compose, and nothing makes them agree: the Eurex product `COPA` is an option
  on WisdomTree Copper settling in USD, while Yahoo's `COPA.MI` is the Milan
  listing in EUR. `IWDA.AS` is the same shape. A mismatch stores the quotes and
  refuses to price them (`iv_status = 'fx_mismatch'`), because converting would
  need an FX rate at the settlement instant that skewer does not collect. A
  provider that cannot name its spot's currency reports None, read as
  agreement.
- **Spot must come from the settlement's session, not from now.** Eurex settles
  yesterday; pricing that against a live spot pushed 252 of BMW's 952 contracts
  below their own no-arbitrage bound, and silently skewed every one that did
  solve. `YahooOptionProvider.fetch_close` exists for this. Yahoo also supplies
  the spot itself, since Eurex publishes none — the two feeds compose.
- **The dividend yield is not optional here.** On BMW (7.0% yield) solving with
  `q=0` costs 58 contracts: 750 solve vs 808.
- **Filter quirks.** `Contracts` requires an `eq` filter on `Product` unless you
  paginate; a range filter (`ge`) cannot be combined with `page` at all; date
  variables are `Date!`, not `String!`; operators are lowercase (`eq`, not `EQ`).

Measured on BMW's 2026-09-07 session: 952 contracts, 808 solved (85%), median
IV 37.5%.

### Known gaps

- **A corporate action breaks the dimension table's immutability, and the break
  is recorded rather than hidden.** Eurex adjusts terms and issues a new ISIN,
  so the adjusted contract is still a new row. `version_nr`, `generation_nr`
  and `original_strike` are stored, and `ContractRepository.link_adjusted`
  points the new row at the one it replaced by matching expiry, right,
  `original_strike` and generation. An ambiguous match writes nothing, because
  NULL and "never adjusted" read the same way and a wrong link does not.
  **Series reads do not stitch across the boundary** — 92 on 100 shares and
  91.49 on 101 are not points on one line — the API returns `supersedes` /
  `superseded_by` and the contract page links them. Live: SAP has 4 adjusted
  contracts out of 1,288; every other product tested is entirely at version 0.
- **No open interest.** It is a fee-based Eurex file, so `option_daily_stats`
  gets NULL rather than a number for European names.

## Euronext (the third venue) — written to spec, never run

`services/option_providers/euronext.py`, built from "EWS API - Derivatives
Markets - Client Specification" v1.4. **It has never touched the live API.**
There is no anonymous or sandbox mode, so it needs an `authKey` from Euronext
London (`EURONEXT_AUTH_KEY`). With the key unset it refuses to poll and rejects
Euronext underlyings at add time, rather than emitting 401s.

- **One contract per request, and no chain endpoint.** The entire spec exposes
  `instrumentDetail` and `intraday`, both per-instrument. There is no list and
  no search. So a chain is an *enumerated grid* — maturities x strikes x rights
  — and a miss is how you learn a strike is not listed. A full chain is several
  hundred requests, which is why `EURONEXT_MAX_REQUESTS` is a hard stop and the
  fetch is semaphore-bounded. This is a licensed commercial gateway.
- **The strike ladder is guessed.** Euronext publishes strike intervals per
  contract class and this API cannot be asked for them, so `strike_step_for`
  brackets by price level. A wrong guess costs wasted requests and a chain with
  holes, never wrong data — every contract that comes back is real.
- **The richest feed of the three.** A genuine order book (`bsBidPx`/`bsAskPx`),
  open interest on both sessions, and a settlement price. No settlement-as-mark
  fallback is needed, unlike Eurex.
- **Identity validates itself.** The identifier is `CODE-C|P-YYYYMM-STRIKE`, so
  the strike is checked against the key the way an OCC symbol is. Eurex cannot
  do this. But the maturity is only a *month*: the real expiry comes from
  `instr.exchEndDt` and is never guessed at the third Friday.
- **The denominators are the one real unknown.** `specFeat[]` carries
  `instrumentDenominator` and `strikePriceDenominator` and the spec never says
  in prose that prices are divided by them. If that is backwards, every price
  is out by 10x or 100x. `domain/euronext.check_strike` is the tripwire: the
  identifier's strike must survive the scaling or the row is dropped.
- Not in the add-time venue auto-detect, because probing costs a burst of
  requests. Euronext underlyings must name `venue` explicitly, and
  `venue_product` takes a `DBRU:ABI` form since a code can list on several
  markets.

## The catalogue path stores no prices

`refresh_contract_catalog` calls `sync_all(catalog_only=True)`, which upserts
the contract dimension and returns before anything is priced or persisted.
That mode exists because cataloguing genuinely should run when the market is
shut, and the book it would find there is yesterday's, frozen and mostly
zero-bid.

It used to call `sync_all(full=False)` and claim in its docstring that a closed
market therefore persisted nothing. It did not: `should_persist` is a pure
function of tenor and slot index and knows nothing about market hours, so the
12:30 UTC run wrote a full pre-market cross-section every day of the week.
That is 1,169 NVDA rows on 2026-09-05. Those rows are still in the database; they are
the one-slot phantom sessions the session-profile picker filters out by slot
count.