"""drop apikey.rate_limit_exempt

Revision ID: e1f2a3b4c5d6
Revises: d8e9f0a1b2c3
Create Date: 2026-10-08

Chat is now exempt from the passthrough rate limit through the
CHAT_API_KEYS setting, so the per-key flag (d8e9f0a1b2c3) is unused.
"""

import sqlalchemy as sa
from alembic import op

revision = "e1f2a3b4c5d6"
down_revision = "d8e9f0a1b2c3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("apikey", "rate_limit_exempt")


def downgrade() -> None:
    op.add_column(
        "apikey",
        sa.Column(
            "rate_limit_exempt",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
