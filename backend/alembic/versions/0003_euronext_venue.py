"""Add Euronext to the venue enum.

One value, no table changes. That is the point of having done 0002: the third
venue costs a single enum member because contract identity, the expiry instant
and provider routing are already data rather than assumptions.

``ALTER TYPE ... ADD VALUE`` is safe inside a transaction on PostgreSQL 12 and
later provided the new value is not *used* in the same transaction, which it is
not here — the first Euronext row can only be written by a later request. The
guard makes the migration re-runnable, since ADD VALUE has no IF NOT EXISTS in
combination with a re-run against an already-migrated database.

Revision ID: 0003
Revises: 0002
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

VENUE_ENUM = "option_venue"
NEW_VALUE = "euronext"


def upgrade() -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_enum e
                JOIN pg_type t ON t.oid = e.enumtypid
                WHERE t.typname = '{VENUE_ENUM}' AND e.enumlabel = '{NEW_VALUE}'
            ) THEN
                ALTER TYPE {VENUE_ENUM} ADD VALUE '{NEW_VALUE}';
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    """PostgreSQL cannot drop a single enum value.

    Removing one means recreating the type and rewriting every column that
    uses it, which would rewrite `option_contracts` wholesale. Since a value
    nothing references is harmless, the honest downgrade is to delete the rows
    that use it and leave the label in place, rather than to pretend the
    schema change is reversible.
    """
    op.execute("DELETE FROM option_contracts WHERE venue = 'euronext'")
    op.execute("UPDATE underlyings SET venue = 'occ' WHERE venue = 'euronext'")
