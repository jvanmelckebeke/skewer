"""IBKR contract ids on contracts and underlyings.

``ibkr_con_id`` is IBKR's handle for a contract, and the only way to ask the
ibkr-svc gateway for a quote. It is a lookup key, not identity: a Eurex row is
still keyed by ``EUX:`` + ISIN, and IBKR publishes no ISIN for Eurex options,
so the link is made by matching terms (see ``services/ibkr/matching.py``).

Nullable with no backfill. NULL means "not matched", which is every OCC row
and any Eurex row whose terms matched zero or several IBKR contracts.

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("option_contracts", sa.Column("ibkr_con_id", sa.BigInteger(), nullable=True))
    # Unique where set: one IBKR contract can back at most one row. A second
    # claim is a matching bug and should fail loudly, not split a series.
    op.create_index(
        "ix_contract_ibkr_con_id",
        "option_contracts",
        ["ibkr_con_id"],
        unique=True,
        postgresql_where=sa.text("ibkr_con_id IS NOT NULL"),
    )
    op.add_column("underlyings", sa.Column("ibkr_con_id", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("underlyings", "ibkr_con_id")
    op.drop_index("ix_contract_ibkr_con_id", table_name="option_contracts")
    op.drop_column("option_contracts", "ibkr_con_id")
