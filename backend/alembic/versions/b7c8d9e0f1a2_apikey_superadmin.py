"""apikey.is_superadmin — may record OTHER users

Revision ID: b7c8d9e0f1a2
Revises: a1b2c3d4e5f6
Create Date: 2026-08-08

Creating/deleting monitoring rules for other users (i.e. recording their
prompts) moves behind this flag; is_admin keeps everything else. Seed via
SQL: UPDATE apikey SET is_superadmin = true WHERE owner_email = '...';
"""

import sqlalchemy as sa
from alembic import op

revision = "b7c8d9e0f1a2"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "apikey",
        sa.Column(
            "is_superadmin",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("apikey", "is_superadmin")
