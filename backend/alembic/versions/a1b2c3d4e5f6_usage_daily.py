"""usage_daily — per-user, per-model token accounting

Replaces per-request Langfuse traces as the source for usage numbers.
Langfuse costs ~11 KB of blob storage per request to record what amounts to
four integers; at expected traffic that is >1 TB/day. This table holds the
same numbers keyed by (day, user, model), so it grows with distinct
combinations rather than requests.

Revision ID: a1b2c3d4e5f6
Revises: f6a7b8c9d0e1
Create Date: 2026-08-07 09:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "usage_daily",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("owner_email", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("model", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("requests", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("prompt_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "completion_tokens", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("day", "owner_email", "model"),
    )
    # The two read patterns: "who used what recently" and "one user's history".
    op.create_index("ix_usage_daily_day", "usage_daily", ["day"])
    op.create_index("ix_usage_daily_owner_day", "usage_daily", ["owner_email", "day"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_usage_daily_owner_day", table_name="usage_daily")
    op.drop_index("ix_usage_daily_day", table_name="usage_daily")
    op.drop_table("usage_daily")
