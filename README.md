# skewer

Self-hosted option-chain collection and analytics. A sibling to
[fibenchi](../fibenchi) — same architecture, same feel — but where fibenchi
tracks an asset's *price history*, skewer tracks an underlying's *option
surface*.

You add `NVDA`; skewer collects its entire option chain on a schedule, solves
implied volatility, computes greeks, and exposes it all over a typed API.

> **Status: early.** The collection core is built and tested end-to-end against
> live data. There is no frontend yet.

## Why this exists

Yahoo Finance serves historical OHLCV for tickers but publishes **no option
history at all** — you can read today's chain and that is it. Options data is
only useful as a series (how did IV move, how did skew steepen, where did open
interest build), so the only way to have that series is to collect it yourself,
continuously. That is skewer's job.

## What it does

- **Chain collection** — full option chains for tracked US underlyings, on an
  adaptive schedule
- **Implied volatility** — re-solved from the mid of a genuine two-sided quote,
  with an explicit "no answer" when none can be implied
- **Greeks** — delta, gamma, vega, theta, rho, computed via Black-Scholes-Merton
  (the provider supplies none)
- **Chain metrics** — ATM IV, 25-delta skew, put/call ratios, max pain, net
  gamma exposure, IV term slope — via a registry, so adding one is one function
- **IV smile & term structure** — per-expiration surface slices
- **Groups** — ported from fibenchi; group membership is the collection switch

## Tech stack

| Layer    | Technology |
|----------|------------|
| Backend  | Python 3.12, FastAPI, SQLAlchemy (async), APScheduler |
| Storage  | PostgreSQL + **TimescaleDB** (hypertables, compression, retention) |
| Compute  | numpy + scipy (vectorised over whole chains) |
| Data     | Yahoo Finance via yahooquery (raw JSON, not the DataFrame helper) |
| Infra    | Docker Compose |

## Quick start

```bash
cp .env.example .env
docker compose up -d
docker compose exec backend alembic upgrade head

# Track an underlying (validates a listed chain exists)
curl -X POST localhost:18100/api/underlyings \
     -H 'content-type: application/json' -d '{"symbol":"NVDA"}'
```

- **API:** http://localhost:18100/api
- **Docs:** http://localhost:18100/docs

Ports are `18100` / `5433` so skewer runs alongside fibenchi without clashing.

## The two decisions that shape everything

### 1. Storage: a star schema on TimescaleDB

Option data is *enormous*. Measured on the real NVDA chain: **3,286 contracts**
across 20 expirations. Sampling every contract every 5 minutes through a session
is ~256,000 rows per day **for one underlying** — about a billion rows a year
for ten, which in plain PostgreSQL is roughly 110 GB.

Two things make that tractable:

**A star schema.** Strike, expiration and right are immutable properties of a
contract's identity — they are literally encoded in its OCC symbol. They live
once in `option_contracts`, so a quote row carries an `int` foreign key and
nothing descriptive. That is a ~40-byte fact row instead of ~120.

*(This is also why not InfluxDB, which was the other candidate. Nearly every
question here joins ticks against contract metadata. A tag-based store would
need strike/expiry/right denormalised onto every point, putting you at hundreds
of thousands of series that grow without bound as contracts expire and new ones
list — the classic cardinality trap. In Timescale a contract is an int FK,
joins are free, and it is still just PostgreSQL: one datastore, one backup, one
migration story, and transactional consistency between contract metadata and
ticks.)*

**Adaptive cadence.** See below.

### 2. Capture: full chain, adaptive cadence

Every poll fetches the **entire** chain, because that is what the provider
offers — Yahoo's endpoint returns all expirations in one request, so fetching
one costs the same as fetching twenty. What varies is what gets **persisted**.

A near-dated option re-prices minute to minute; a 2028 LEAPS barely moves in a
day. Sampling both at 5 minutes stores the same LEAPS number 78 times. So
contracts are bucketed by days-to-expiry:

| Bucket  | DTE      | Sampled          |
|---------|----------|------------------|
| `gamma` | ≤ 7      | every 5 min      |
| `front` | ≤ 30     | every 15 min     |
| `mid`   | ≤ 90     | every 30 min     |
| `leaps` | > 90     | once, at the close |

**Every strike is still captured at every sampled instant**, so smiles and skew
are never truncated — only the *time* resolution varies, matched to how fast
each tenor actually moves.

Measured against the real NVDA chain this stores **81% fewer rows** — 48k
instead of 256k per session — because 68% of that chain is LEAPS. For ten
underlyings that is ~121M rows/year instead of ~1B, and compressed it is a few
GB rather than 110.

Timestamps are snapped to a wall-clock grid, so every contract captured in one
poll shares a timestamp. A stored snapshot is a genuine **simultaneous
cross-section**, which is what makes an IV surface meaningful rather than a
smear.

## Data quality: what the provider gets wrong

Measured on a live NVDA chain, not assumed:

| Finding | Consequence |
|---|---|
| **No greeks at all.** The payload has `bid, ask, lastPrice, volume, openInterest, impliedVolatility, inTheMoney`. | Every greek is computed locally. |
| **49% of contracts report IV = 1e-05** — the provider's solver failing and reporting its bracket floor. Outliers reach **18.0** (1800% vol). | Its IV is stored for comparison only; skewer solves its own. |
| **`bid`/`ask` are `0.0` outside regular hours** — 2,954 of 3,286 contracts while in pre-market. | Zero maps to NULL at the provider boundary. A zero bid is "nobody is quoting", not "worth nothing". |
| **`lastPrice` can be weeks stale.** 12% of the chain quotes a last price *below intrinsic value*. | Never used as a pricing basis; stored with its trade timestamp so staleness is visible. |
| **US listings only.** | Adding a European ticker is rejected at creation, not silently collected as nothing. |

### Honest NULLs

Implied volatility is solved by bisection over the pricing function, but
convergence is not the test — **identifiability** is. A deep-ITM option is
almost pure intrinsic value, so *every* sigma reproduces its price to within a
fraction of a tick; a solver will happily return whichever bracket edge it
drifted to. That is exactly how the provider ends up publishing `1e-05`.

So skewer requires that a full volatility point move the model price by at least
half a quote tick (vega ≥ 0.5). Below that it returns NULL with a reason code —
`no_quote`, `no_vega`, `below_intr`, `above_bound`, `no_root`, `expired`. A
contract with no IV also has no greeks, rather than a zero that plots.

## Development

```bash
docker compose up -d
docker compose exec backend pytest
docker compose exec backend ruff check app/

# Collection-path tests need PostgreSQL (dialect-specific upserts):
SKEWER_TEST_DATABASE_URL=postgresql+asyncpg://skewer:skewer@db:5432/skewer_test pytest
```

## Configuration

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://skewer:skewer@db:5432/skewer` | Connection string |
| `BASE_POLL_MINUTES` | `5` | The poll grid; every cadence bucket is a multiple |
| `EOD_SNAPSHOT_CRON` | `10 21 * * 1-5` | Full-chain snapshot, UTC (~40min after the US close) |
| `RISK_FREE_RATE` | `0.04` | Annualised, ACT/365, for the greeks |
| `LOG_LEVEL` | `INFO` | |

> **No authentication.** Deploy behind a reverse proxy or VPN, and change the
> default database credentials.

## Known limitations

- **US equities only** — a provider-imposed limit; the `OptionProvider` ABC is
  the seam where another source plugs in.
- **Quotes are ~15 minutes delayed** (Yahoo).
- **Flat risk-free rate** rather than a term structure. At equity-option tenors
  the greeks are far more sensitive to IV than to a few bp of rate.
- **Dividend yield defaults to 0** and is set per underlying.
- **No frontend yet.**
