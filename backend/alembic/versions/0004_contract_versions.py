"""Record a Eurex contract's version, and link it to the one it replaced.

The dimension table assumes strike, expiration and right are immutable. That
holds on OCC, where the terms are encoded in the symbol. It does not hold on
Eurex: a corporate action adjusts the terms and issues a new ISIN, so the
adjusted contract arrives as an unrelated new row while the old one keeps its
whole quote history.

Four columns:

``version_nr``       0 for a contract that has never been adjusted.
``generation_nr``    the product generation the contract was issued under.
``original_strike``  the strike before adjustment, published by Eurex.
``supersedes_contract_id``  the row this one replaced, when it can be
                     identified unambiguously. Self-referential and nullable,
                     because the predecessor may predate collection entirely.

Live example, SAP on 2026-09-09: 1,284 contracts at VersionNr 0 /
GenerationNr 8, and 4 at VersionNr 1 / GenerationNr 7 with Strike 91.49
against OriginalStrike 92 and a ContractSize of 101 rather than 100.

All four are nullable with no backfill. They are provider facts, and the only
provider that publishes them refreshes every contract it lists on the next
poll.

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("option_contracts", sa.Column("version_nr", sa.Integer(), nullable=True))
    op.add_column("option_contracts", sa.Column("generation_nr", sa.Integer(), nullable=True))
    op.add_column(
        "option_contracts",
        sa.Column("original_strike", sa.Numeric(14, 4), nullable=True),
    )
    op.add_column(
        "option_contracts",
        sa.Column("supersedes_contract_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_contract_supersedes",
        "option_contracts", "option_contracts",
        ["supersedes_contract_id"], ["id"],
        ondelete="SET NULL",
    )
    # The predecessor lookup: same underlying and expiry, matched on the
    # pre-adjustment strike. Deliberately not unique — an ambiguous match is
    # resolved by refusing to link, not by a constraint violation.
    op.create_index(
        "ix_contract_adjustment_match",
        "option_contracts",
        ["underlying_id", "expiration", "right", "strike", "generation_nr"],
    )


def downgrade() -> None:
    op.drop_index("ix_contract_adjustment_match", table_name="option_contracts")
    op.drop_constraint("fk_contract_supersedes", "option_contracts", type_="foreignkey")
    op.drop_column("option_contracts", "supersedes_contract_id")
    op.drop_column("option_contracts", "original_strike")
    op.drop_column("option_contracts", "generation_nr")
    op.drop_column("option_contracts", "version_nr")
