"""apikey.rate_limit_rpm — durable per-user rate limit

Revision ID: f7a8b9c0d1e2
Revises: e1f2a3b4c5d6
Create Date: 2026-10-23

Per-user limits were only Redis keys (rl:limit:<sha256(key)[:16]>), set by
hand. Prod Redis is an LRU cache on an emptyDir, so an override could be
evicted or lost on restart, went stale on key rotation, and was easy to
write under the wrong hash. NULL keeps the default; set it with
PUT /v1/admin/rate-limits/{email}.
"""

import sqlalchemy as sa
from alembic import op

revision = "f7a8b9c0d1e2"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "apikey",
        sa.Column("rate_limit_rpm", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("apikey", "rate_limit_rpm")
