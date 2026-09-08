from datetime import date, datetime
from typing import Optional

from sqlmodel import SQLModel, Field, UniqueConstraint


class APIKey(SQLModel, table=True):
    key: str = Field(primary_key=True)
    budget: int = Field(default=1000)
    created_at: datetime = Field(default=datetime.now())
    updated_at: datetime = Field(default=datetime.now())
    owner_email: str = Field(default="")
    # The user's real name, as the IdP reports it (`name` claim), recorded
    # whenever they load their profile. The public model catalogue credits
    # launchers by name instead of publishing their address, and this is
    # where that name comes from when we have it — see
    # :mod:`backend.services.identity_service`. Empty for a key whose owner
    # has only ever used the API.
    owner_name: str = Field(default="")
    # Grants /v1/admin/* access. Set via SQL (or a future admin UI); when the
    # IdP (Authentik) exposes a group claim, require_admin can additionally
    # honour an admin group membership — this flag stays the durable base.
    is_admin: bool = Field(default=False)
    # Grants creating/deleting monitoring rules for OTHER users — the power
    # to record someone's prompts, held far more narrowly than is_admin.
    # Set via SQL only.
    is_superadmin: bool = Field(default=False)


class PerfBenchmark(SQLModel, table=True):
    """Running averages for the Performance page, per (month, model,
    served-on). Monthly buckets so old data ages out of the page instead
    of biasing an all-time average forever. Maintained by
    MetricsCollector; replaced the disabled Firestore sync."""

    __tablename__ = "perf_benchmark"
    __table_args__ = (
        UniqueConstraint("month", "model", "hardware", name="uq_perf_benchmark_key"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    month: str  # "YYYY-MM"
    model: str
    hardware: str  # real hardware (self-hosted) or provider label (passthrough)
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


class UsageDaily(SQLModel, table=True):
    """Per-user, per-model token accounting, aggregated by day.

    Rows scale with distinct (day, user, model) combinations rather than
    requests, so a batch of a million calls from one user against one model
    is a single row. Input and output tokens stay separate: a long prompt
    answered in five tokens costs nothing like the reverse, and a combined
    total hides the ratio that matters for capacity planning.
    """

    __tablename__ = "usage_daily"

    day: date = Field(primary_key=True)
    owner_email: str = Field(primary_key=True)
    model: str = Field(primary_key=True)  # public (namespaced) id
    requests: int = Field(default=0)
    prompt_tokens: int = Field(default=0)
    completion_tokens: int = Field(default=0)
    updated_at: datetime = Field(default_factory=datetime.now)


class ModelAccessOverride(SQLModel, table=True):
    """A model's access policy after someone changed it post-launch.

    OpenTela labels are stamped at peer start and immutable for the life of
    the SLURM job, which makes the launch-time `--authorization` value a
    decision you cannot revisit without relaunching. This table is the
    mutable overlay: while a row exists it REPLACES the peer's
    `authorization` label as the policy the gateway enforces, and deleting
    it ("Reset") hands authority straight back to the label. The label is
    never rewritten, so the original intent stays recoverable.

    Keyed by `launch_id` — the UUID label SML stamps on every launch — and
    NOT by model name. Names are a shared namespace anyone can relaunch
    into, and an override that outlived its job must never re-attach itself
    to whatever comes back under the same name; scoping the row to one
    launch instance makes that impossible, since a relaunch draws a fresh
    UUID and therefore starts clean at its own label. It also makes garbage
    collection obvious: a row whose launch_id is absent from the DNT is
    dead and can be pruned whenever.
    """

    __tablename__ = "model_access_override"

    launch_id: str = Field(primary_key=True)
    # Denormalised from the DNT at write time, for the audit trail: once the
    # job ends its labels go with it, and a bare UUID then tells nobody
    # which model was changed or whose it was.
    model_id: str = Field(index=True)
    owner_email: str = Field(default="")
    # Same grammar as the label it stands in for: "public", or a
    # comma-separated email list. Stored canonicalised (stripped,
    # lowercased, de-duplicated) so it compares equal to a label meaning
    # the same thing.
    policy: str
    updated_by: str = Field(default="")
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
