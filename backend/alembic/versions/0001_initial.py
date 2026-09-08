"""Initial schema: underlyings, groups, contracts, quotes, daily stats.

Also converts the two fact tables into TimescaleDB hypertables and installs
compression + retention policies. Every Timescale statement is guarded on the
extension actually being present, so the schema still applies to a plain
PostgreSQL instance — it simply gets ordinary tables, which is slower at scale
but entirely correct.

Revision ID: 0001
Revises:
"""

import logging

import sqlalchemy as sa
from alembic import op

logger = logging.getLogger("alembic.runtime.migration")

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# Chunk interval for the quote hypertable. One day per chunk keeps each chunk
# in the low hundreds of thousands of rows at a realistic portfolio size, which
# is the range where Timescale's chunk-exclusion actually pays off: a "chain at
# 14:35 today" query touches exactly one chunk instead of scanning a year.
CHUNK_INTERVAL = "1 day"

# Compress chunks older than this. A week is comfortably past the point where
# a chunk still receives writes (upserts only ever target the current slot),
# and compressed chunks in Timescale are far more expensive to modify than to
# read — so the window exists to guarantee writes never touch one.
COMPRESS_AFTER = "7 days"

# Retention. Deliberately generous and deliberately explicit: two years of
# option quotes is a lot of rows but it is also the minimum horizon over which
# IV rank and seasonal skew mean anything. Compressed, it is a few GB.
RETAIN_QUOTES = "2 years"


def _has_timescale(conn) -> bool:
    return bool(
        conn.execute(
            sa.text("SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb'")
        ).scalar()
    )


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    op.create_table(
        "underlyings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("symbol", sa.String(20), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False, server_default=""),
        sa.Column("currency", sa.String(10), nullable=False, server_default="USD"),
        sa.Column("dividend_yield", sa.Numeric(8, 6), nullable=False, server_default="0"),
        sa.Column("dividend_yield_is_manual", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_eod_snapshot_on", sa.Date, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index("ix_underlyings_symbol", "underlyings", ["symbol"], unique=True)

    op.create_table(
        "groups",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("icon", sa.String(50), nullable=True),
        sa.Column("is_default", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("position", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )

    op.create_table(
        "group_underlyings",
        sa.Column("group_id", sa.Integer, sa.ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("underlying_id", sa.Integer, sa.ForeignKey("underlyings.id", ondelete="CASCADE"), primary_key=True),
    )

    # Not pre-created: op.create_table emits the CREATE TYPE itself when it
    # first encounters the Enum column. Creating it here as well made the
    # migration fail with DuplicateObjectError on a clean database.
    right_enum = sa.Enum("call", "put", name="option_right")

    op.create_table(
        "option_contracts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("underlying_id", sa.Integer, sa.ForeignKey("underlyings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("occ_symbol", sa.String(24), nullable=False, unique=True),
        sa.Column("expiration", sa.Date, nullable=False),
        sa.Column("strike", sa.Numeric(14, 4), nullable=False),
        sa.Column("right", right_enum, nullable=False),
        sa.Column("contract_size", sa.String(10), nullable=False, server_default="REGULAR"),
        sa.Column("currency", sa.String(10), nullable=False, server_default="USD"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_option_contracts_underlying_id", "option_contracts", ["underlying_id"])
    op.create_index("ix_option_contracts_occ_symbol", "option_contracts", ["occ_symbol"], unique=True)
    op.create_index("ix_contract_underlying_exp", "option_contracts", ["underlying_id", "expiration", "strike"])
    op.create_index("ix_contract_expiration", "option_contracts", ["expiration"])

    # The fact table. No surrogate id — the natural (contract_id, ts) key is
    # also the upsert conflict target and the only PK a hypertable can carry
    # (Timescale requires the partition column in every unique index).
    op.create_table(
        "option_quotes",
        sa.Column("contract_id", sa.Integer, sa.ForeignKey("option_contracts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bid", sa.Numeric(14, 4), nullable=True),
        sa.Column("ask", sa.Numeric(14, 4), nullable=True),
        sa.Column("mark", sa.Numeric(14, 4), nullable=True),
        sa.Column("last", sa.Numeric(14, 4), nullable=True),
        sa.Column("last_trade_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("volume", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("spot", sa.Numeric(14, 4), nullable=True),
        sa.Column("iv", sa.Numeric(10, 6), nullable=True),
        sa.Column("iv_provider", sa.Numeric(10, 6), nullable=True),
        sa.Column("iv_status", sa.String(16), nullable=False, server_default="ok"),
        sa.PrimaryKeyConstraint("contract_id", "ts", name="pk_option_quotes"),
    )

    op.create_table(
        "underlying_quotes",
        sa.Column("underlying_id", sa.Integer, sa.ForeignKey("underlyings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("price", sa.Numeric(14, 4), nullable=False),
        sa.Column("bid", sa.Numeric(14, 4), nullable=True),
        sa.Column("ask", sa.Numeric(14, 4), nullable=True),
        sa.Column("previous_close", sa.Numeric(14, 4), nullable=True),
        sa.Column("market_state", sa.String(12), nullable=True),
        sa.PrimaryKeyConstraint("underlying_id", "ts", name="pk_underlying_quotes"),
    )

    op.create_table(
        "option_daily_stats",
        sa.Column("contract_id", sa.Integer, sa.ForeignKey("option_contracts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("session_date", sa.Date, nullable=False),
        sa.Column("open_interest", sa.BigInteger, nullable=True),
        sa.Column("volume", sa.BigInteger, nullable=True),
        sa.Column("close_bid", sa.Numeric(14, 4), nullable=True),
        sa.Column("close_ask", sa.Numeric(14, 4), nullable=True),
        sa.Column("close_mark", sa.Numeric(14, 4), nullable=True),
        sa.Column("close_last", sa.Numeric(14, 4), nullable=True),
        sa.Column("close_spot", sa.Numeric(14, 4), nullable=True),
        sa.Column("close_iv", sa.Numeric(10, 6), nullable=True),
        sa.Column("close_iv_provider", sa.Numeric(10, 6), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("contract_id", "session_date", name="pk_option_daily_stats"),
    )
    op.create_index("ix_daily_session_contract", "option_daily_stats", ["session_date", "contract_id"])

    # Seed the default group. Exactly one row carries is_default; it is the
    # container new underlyings land in when no group is named.
    op.execute(
        "INSERT INTO groups (name, description, icon, is_default, position) "
        "VALUES ('Watchlist', 'Default collection group', 'eye', true, 0)"
    )

    if not is_postgres:
        return

    if not _has_timescale(bind):
        logger.warning(
            "TimescaleDB extension is not available — option_quotes and underlying_quotes "
            "were created as ordinary tables. They are correct but will not compress, and "
            "at a realistic collection rate (~120M rows/year for 10 underlyings) that is "
            "roughly 10-20x more disk. Install timescaledb and re-run this migration's "
            "hypertable block to convert."
        )
        return

    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE")

    for table in ("option_quotes", "underlying_quotes"):
        op.execute(
            f"SELECT create_hypertable('{table}', 'ts', "
            f"chunk_time_interval => INTERVAL '{CHUNK_INTERVAL}', migrate_data => TRUE)"
        )

    # Compression. `segmentby = contract_id` is the setting that makes this
    # work: it groups every row for one contract into a single compressed
    # batch, so the columns that barely change across a day (spot, strike-
    # adjacent values, iv_status) collapse to almost nothing, and a
    # single-contract time-series read decompresses only that segment.
    # `orderby ts` lets the timestamp column delta-encode.
    op.execute(
        "ALTER TABLE option_quotes SET ("
        "timescaledb.compress, "
        "timescaledb.compress_segmentby = 'contract_id', "
        "timescaledb.compress_orderby = 'ts DESC')"
    )
    op.execute(
        "ALTER TABLE underlying_quotes SET ("
        "timescaledb.compress, "
        "timescaledb.compress_segmentby = 'underlying_id', "
        "timescaledb.compress_orderby = 'ts DESC')"
    )
    op.execute(f"SELECT add_compression_policy('option_quotes', INTERVAL '{COMPRESS_AFTER}')")
    op.execute(f"SELECT add_compression_policy('underlying_quotes', INTERVAL '{COMPRESS_AFTER}')")

    op.execute(f"SELECT add_retention_policy('option_quotes', INTERVAL '{RETAIN_QUOTES}')")
    # underlying_quotes and option_daily_stats are tiny by comparison and are
    # the long-horizon reference series (IV rank needs a year+), so neither
    # gets a retention policy.


def downgrade() -> None:
    op.drop_table("option_daily_stats")
    op.drop_table("underlying_quotes")
    op.drop_table("option_quotes")
    op.drop_table("option_contracts")
    op.drop_table("group_underlyings")
    op.drop_table("groups")
    op.drop_table("underlyings")
    bind = op.get_bind()
    sa.Enum(name="option_right").drop(bind, checkfirst=True)
