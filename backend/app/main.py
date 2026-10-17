import logging
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.responses import FileResponse

from app.background_tasks import all_tasks
from app.config import settings
from app.routers import chain, groups, system, underlyings
from app.services.option_providers import init_option_provider

# App loggers write through the root logger, which neither uvicorn nor docker
# configures — without this every logger.info() (poll summaries, IV solve
# counts, skipped symbols) is silently discarded and only WARNING+ reaches
# `docker logs`. basicConfig is a no-op when handlers already exist (pytest),
# and uvicorn's own loggers don't propagate to root, so nothing is duplicated.
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()


from contextlib import asynccontextmanager  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_option_provider()

    # Schedule whatever registered itself in app/background_tasks/jobs.py. A
    # task whose trigger factory returns None is disabled — it logged why — but
    # never takes the others down with it.
    for task in all_tasks():
        trigger = task.resolve_trigger()
        if trigger is None:
            continue
        scheduler.add_job(task.func, trigger, id=task.id)
    scheduler.start()
    logger.info(
        "Scheduler started with %d background jobs (base poll: %dmin, EOD cron: %s)",
        len(scheduler.get_jobs()), settings.base_poll_minutes, settings.eod_snapshot_cron,
    )

    yield

    scheduler.shutdown(wait=False)
    from app.database import engine

    await engine.dispose()


app = FastAPI(
    title="Skewer",
    summary="Option-chain collection and analytics for tracked underlyings.",
    description=(
        "Skewer collects option chains for tracked US-listed underlyings and turns them "
        "into greeks and surface metrics.\n\n"
        "**How collection works.** Every poll fetches an underlying's *entire* chain in one "
        "provider request, because that is what the provider offers. What varies is what gets "
        "*persisted*: contracts are bucketed by days-to-expiry and sampled at a cadence matched "
        "to how fast they actually re-price — 5 minutes inside a week, 15 inside a month, 30 "
        "inside a quarter, and once daily beyond that. Every strike is still covered at every "
        "sampled instant, so smiles and skew are never truncated; measured on a real NVDA chain "
        "this stores ~81% fewer rows than sampling everything at 5 minutes.\n\n"
        "**Timestamps are snapped to a grid**, so all contracts captured in one poll share a "
        "timestamp and a stored snapshot is a genuine simultaneous cross-section.\n\n"
        "**Greeks are computed, not fetched.** The provider supplies no greeks at all, and its "
        "implied volatility is unreliable (49% of a real NVDA chain came back at 1e-05, its "
        "solver's failure sentinel). Skewer re-solves IV from the mid of a genuine two-sided "
        "quote and returns NULL — with a reason code — wherever no volatility can be implied, "
        "rather than publishing a number that plots but means nothing.\n\n"
        "**US listings only.** The provider has no option data for European tickers; adding one "
        "is rejected at creation time rather than silently collecting nothing."
    ),
    version="0.1.0",
    lifespan=lifespan,
    openapi_tags=[
        {
            "name": "underlyings",
            "description": "Tracked underlyings. Adding one validates that a listed option chain exists.",
        },
        {
            "name": "groups",
            "description": (
                "Named collections of underlyings. Group membership is the collection switch: "
                "an underlying in no group is not polled, but keeps its history."
            ),
        },
        {
            "name": "chains",
            "description": (
                "Chain snapshots with computed greeks, IV smiles, ATM term structure, and "
                "registry-driven chain metrics (ATM IV, 25-delta skew, put/call ratios, max "
                "pain, net gamma exposure)."
            ),
        },
        {"name": "system", "description": "Health and collector diagnostics."},
    ],
)

app.include_router(underlyings.router)
app.include_router(groups.router)
app.include_router(chain.router)
app.include_router(system.router)

# nginx gzipped responses in front of the dev stack; production has no nginx,
# and a whole chain is thousands of JSON rows.
app.add_middleware(GZipMiddleware, minimum_size=1024)

# The root Dockerfile builds the SPA into /app/static. In dev that directory
# doesn't exist and the frontend's own nginx serves it, so the mount is skipped.
_SPA_DIR = Path(__file__).resolve().parent.parent / "static"

if (_SPA_DIR / "index.html").exists():
    app.mount("/assets", StaticFiles(directory=_SPA_DIR / "assets"), name="static-assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def _spa_fallback(path: str):
        file = _SPA_DIR / path
        if file.is_file() and file.resolve().is_relative_to(_SPA_DIR.resolve()):
            return FileResponse(file)
        return FileResponse(_SPA_DIR / "index.html")
