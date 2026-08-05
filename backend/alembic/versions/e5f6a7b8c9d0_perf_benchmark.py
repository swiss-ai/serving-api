"""perf_benchmark table — replaces Firestore for /v1/perf aggregates

The MetricsCollector always computed per-(model, hardware, concurrency)
running averages of TTFT/latency/throughput but synced them to Firebase,
which has been disabled for months — leaving the Performance page empty.
Store them in our own postgres instead.

Revision ID: e5f6a7b8c9d0
Revises: c2d3e4f5a6b7
Create Date: 2026-08-05 19:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, Sequence[str], None] = "c2d3e4f5a6b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "perf_benchmark",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("month", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("model", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("hardware", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("concurrency", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("avg_ttft", sa.Float(), nullable=False),
        sa.Column("avg_latency", sa.Float(), nullable=False),
        sa.Column("avg_throughput", sa.Float(), nullable=False),
        sa.Column("last_updated", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "month", "model", "hardware", "concurrency", name="uq_perf_benchmark_key"
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("perf_benchmark")
