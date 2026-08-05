"""drop perf_benchmark.concurrency — the metric was misleading

The recorded value was a single uvicorn worker's global in-flight counter
(gateway-wide, cross-model), not the engine's batch occupancy — so the
bucketed "concurrency" dimension implied a load/performance curve it never
measured. Real concurrency curves live in the engines' Prometheus metrics.

Wipes the hours-old table (dev held one test row) rather than merging
buckets: aggregates rebuild from live traffic immediately.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-08-05 22:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, Sequence[str], None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("DELETE FROM perf_benchmark")
    op.drop_constraint("uq_perf_benchmark_key", "perf_benchmark", type_="unique")
    op.drop_column("perf_benchmark", "concurrency")
    op.create_unique_constraint(
        "uq_perf_benchmark_key", "perf_benchmark", ["month", "model", "hardware"]
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("uq_perf_benchmark_key", "perf_benchmark", type_="unique")
    op.add_column(
        "perf_benchmark",
        sa.Column(
            "concurrency",
            sqlmodel.sql.sqltypes.AutoString(),
            nullable=False,
            server_default="1",
        ),
    )
    op.create_unique_constraint(
        "uq_perf_benchmark_key",
        "perf_benchmark",
        ["month", "model", "hardware", "concurrency"],
    )
