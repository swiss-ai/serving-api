"""apikey.rate_limit_exempt — keys the passthrough limiter skips

Revision ID: d8e9f0a1b2c3
Revises: b7c8d9e0f1a2
Create Date: 2026-10-05

Open WebUI (chat) reaches serving-api through one shared connection key,
so the per-key passthrough limit (RATE_LIMIT_RPM) throttles every chat
user together. The Redis override (rl:limit:<identity> = 0) can lift it,
but Redis is an LRU cache on an emptyDir — the override is lost on a
restart. This flag is the durable exemption. Seed via SQL:
UPDATE apikey SET rate_limit_exempt = true WHERE key = '<chat key>';
"""

import sqlalchemy as sa
from alembic import op

revision = "d8e9f0a1b2c3"
down_revision = "b7c8d9e0f1a2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "apikey",
        sa.Column(
            "rate_limit_exempt",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("apikey", "rate_limit_exempt")
