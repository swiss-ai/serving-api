"""dev smoke test — TEMPORARY, will be reverted before any prod release

Demonstrates the in-cluster alembic pipeline (migrate initContainer in
rob-poc dev). Creates one throwaway table; downgrade drops it.

Revision ID: cafe00000001
Revises: 6d8e1c6ed7b5
Create Date: 2026-08-05 15:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "cafe00000001"
down_revision: Union[str, Sequence[str], None] = "6d8e1c6ed7b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "dev_migration_smoke_test",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("note", sa.String(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("dev_migration_smoke_test")
