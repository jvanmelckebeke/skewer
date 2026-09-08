"""Multi-venue contracts: Eurex alongside OCC.

Four changes, all to the dimension tables. The fact tables are untouched, which
is the point of the star schema: adding a whole second exchange does not
rewrite a single row of ``option_quotes``.

1. ``option_contracts.occ_symbol`` becomes ``symbol`` and widens 24 -> 32.
   The column was never really "the OCC symbol", it was "the venue's natural
   key", and it only looked like the former because there was one venue.
   Existing OCC values are unchanged by the rename, so no data moves; Eurex
   keys arrive as ``EUX:`` + ISIN, which is 16 characters and fits.

2. ``venue`` and ``exercise_style`` are added to ``option_contracts``. Both
   backfill to the values that were previously implicit for every existing
   row: everything already collected is US-listed and American-style.

3. ``contract_size`` changes from Yahoo's 'REGULAR'/'MINI' label to the
   integer share multiplier, because the label is a US-ism with no Eurex
   analogue while the number means the same thing everywhere.

4. ``underlyings`` gains ``venue`` and ``venue_product``, which is what lets
   one process poll Yahoo and Eurex side by side instead of switching
   globally on OPTION_PROVIDER.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

VENUE_ENUM = "option_venue"
EXERCISE_ENUM = "option_exercise_style"


def upgrade() -> None:
    bind = op.get_bind()

    # Create the enum types explicitly and pass create_type=False below.
    # Letting add_column create them implicitly *and* declaring them here is
    # the DuplicateObjectError this project has already hit once; doing it in
    # one place is the fix.
    venue = sa.Enum("occ", "eurex", name=VENUE_ENUM)
    exercise = sa.Enum("european", "american", name=EXERCISE_ENUM)
    venue.create(bind, checkfirst=True)
    exercise.create(bind, checkfirst=True)

    # --- option_contracts -------------------------------------------------
    op.alter_column(
        "option_contracts", "occ_symbol",
        new_column_name="symbol",
        existing_type=sa.String(24), type_=sa.String(32),
        existing_nullable=False,
    )

    # PostgreSQL keeps index and constraint names across a column rename, so
    # without this the unique index on `symbol` is still called
    # `..._occ_symbol_key`. Purely cosmetic, but a misleading index name is
    # exactly the kind of thing that costs someone twenty minutes later.
    op.execute("ALTER INDEX IF EXISTS ix_option_contracts_occ_symbol "
               "RENAME TO ix_option_contracts_symbol")
    op.execute("ALTER TABLE option_contracts "
               "RENAME CONSTRAINT option_contracts_occ_symbol_key "
               "TO option_contracts_symbol_key")

    op.add_column(
        "option_contracts",
        sa.Column(
            "venue",
            sa.Enum(name=VENUE_ENUM, create_type=False),
            nullable=False, server_default="occ",
        ),
    )
    op.add_column(
        "option_contracts",
        sa.Column(
            "exercise_style",
            sa.Enum(name=EXERCISE_ENUM, create_type=False),
            nullable=False, server_default="american",
        ),
    )

    # 'REGULAR' -> 100, 'MINI' -> 10. USING is required because PostgreSQL
    # will not implicitly cast varchar to integer, and the CASE is what
    # carries the two known labels across. Anything else becomes 100, which is
    # the correct default for every listed equity option skewer has seen.
    op.execute(
        """
        ALTER TABLE option_contracts
            ALTER COLUMN contract_size DROP DEFAULT,
            ALTER COLUMN contract_size TYPE integer
                USING CASE upper(contract_size)
                    WHEN 'MINI' THEN 10
                    WHEN 'REGULAR' THEN 100
                    ELSE 100
                END,
            ALTER COLUMN contract_size SET DEFAULT 100
        """
    )

    # --- underlyings ------------------------------------------------------
    op.add_column(
        "underlyings",
        sa.Column(
            "venue",
            sa.Enum(name=VENUE_ENUM, create_type=False),
            nullable=False, server_default="occ",
        ),
    )
    op.add_column(
        "underlyings",
        sa.Column("venue_product", sa.String(20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("underlyings", "venue_product")
    op.drop_column("underlyings", "venue")

    op.execute(
        """
        ALTER TABLE option_contracts
            ALTER COLUMN contract_size DROP DEFAULT,
            ALTER COLUMN contract_size TYPE varchar(10)
                USING CASE contract_size WHEN 10 THEN 'MINI' ELSE 'REGULAR' END,
            ALTER COLUMN contract_size SET DEFAULT 'REGULAR'
        """
    )

    op.drop_column("option_contracts", "exercise_style")
    op.drop_column("option_contracts", "venue")

    op.execute("ALTER INDEX IF EXISTS ix_option_contracts_symbol "
               "RENAME TO ix_option_contracts_occ_symbol")
    op.execute("ALTER TABLE option_contracts "
               "RENAME CONSTRAINT option_contracts_symbol_key "
               "TO option_contracts_occ_symbol_key")

    # Eurex rows cannot be represented in a single-venue schema, and their
    # keys do not fit varchar(24) either. Dropping them is the only honest
    # downgrade; the alternative is a rename that fails on the first EUX: row.
    op.execute("DELETE FROM option_contracts WHERE symbol LIKE 'EUX:%'")
    op.alter_column(
        "option_contracts", "symbol",
        new_column_name="occ_symbol",
        existing_type=sa.String(32), type_=sa.String(24),
        existing_nullable=False,
    )

    bind = op.get_bind()
    sa.Enum(name=EXERCISE_ENUM).drop(bind, checkfirst=True)
    sa.Enum(name=VENUE_ENUM).drop(bind, checkfirst=True)
