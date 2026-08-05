from datetime import datetime
from typing import Optional

from sqlmodel import SQLModel, Field, UniqueConstraint


class APIKey(SQLModel, table=True):
    key: str = Field(primary_key=True)
    budget: int = Field(default=1000)
    created_at: datetime = Field(default=datetime.now())
    updated_at: datetime = Field(default=datetime.now())
    owner_email: str = Field(default="")
    # Grants /v1/admin/* access. Set via SQL (or a future admin UI); when the
    # IdP (Authentik) exposes a group claim, require_admin can additionally
    # honour an admin group membership — this flag stays the durable base.
    is_admin: bool = Field(default=False)


class PerfBenchmark(SQLModel, table=True):
    """Running averages for the Performance page, per
    (month, model, hardware, concurrency bucket). Monthly buckets so old
    data ages out of the page instead of biasing an all-time average
    forever. Maintained by MetricsCollector; replaced the disabled
    Firestore sync."""

    __tablename__ = "perf_benchmark"
    __table_args__ = (
        UniqueConstraint(
            "month", "model", "hardware", "concurrency", name="uq_perf_benchmark_key"
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    month: str  # "YYYY-MM"
    model: str
    hardware: str
    concurrency: str  # bucket: '1' | '2-10' | '11-50' | '51-100' | '101+'
    count: int = Field(default=0)
    avg_ttft: float = Field(default=0.0)
    avg_latency: float = Field(default=0.0)
    avg_throughput: float = Field(default=0.0)
    last_updated: datetime = Field(default_factory=datetime.now)


class UserMonitoringRule(SQLModel, table=True):
    """Turns on Langfuse tracing for one user's requests.

    One rule per (owner_email, source): an admin-imposed rule and a self
    opt-in can coexist; the effective level is the max of the active ones.
    Rules always expire (TTL presets enforced at the API layer) so
    monitoring can never be left on forever.
    """

    __tablename__ = "user_monitoring_rule"
    __table_args__ = (
        UniqueConstraint("owner_email", "source", name="uq_monitoring_owner_source"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    owner_email: str = Field(index=True)
    level: str  # 'metadata' | 'full'
    source: str  # 'admin' | 'self'
    expires_at: datetime
    created_by: str
    note: str = Field(default="")
    created_at: datetime = Field(default_factory=datetime.now)
