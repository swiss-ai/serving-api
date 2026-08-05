"""Per-user monitoring rules: who gets traced, at what level, until when.

Rules live in user_monitoring_rule (see the model docstring for semantics).
The hot-path lookup (get_effective_level) is cached for ~30s per user so
chat/completions requests cost zero extra DB queries in the steady state —
same ttl_hash idiom as metrics_service.
"""

from datetime import datetime, timedelta
from typing import Optional

from sqlmodel import Session, select

from backend.models.entities import APIKey, UserMonitoringRule
from backend.services.metrics_service import get_ttl_hash

# Fixed lifetimes — the API accepts only these, never a raw timestamp, so a
# rule that outlives its purpose is impossible by construction.
TTL_CHOICES = {
    "1h": timedelta(hours=1),
    "6h": timedelta(hours=6),
    "1d": timedelta(days=1),
    "7d": timedelta(days=7),
    "90d": timedelta(days=90),
}

LEVEL_RANK = {"metadata": 1, "full": 2}
SOURCES = ("admin", "self")

_effective_level_cache: dict = {}
_owner_email_cache: dict = {}


def upsert_rule(
    engine,
    owner_email: str,
    level: str,
    source: str,
    ttl: str,
    created_by: str,
    note: str = "",
) -> UserMonitoringRule:
    if level not in LEVEL_RANK:
        raise ValueError(f"level must be one of {sorted(LEVEL_RANK)}")
    if source not in SOURCES:
        raise ValueError(f"source must be one of {SOURCES}")
    if ttl not in TTL_CHOICES:
        raise ValueError(f"ttl must be one of {sorted(TTL_CHOICES)}")

    with Session(engine) as session:
        rule = session.exec(
            select(UserMonitoringRule)
            .where(UserMonitoringRule.owner_email == owner_email)
            .where(UserMonitoringRule.source == source)
        ).first()
        if rule is None:
            rule = UserMonitoringRule(
                owner_email=owner_email,
                level=level,
                source=source,
                expires_at=datetime.now() + TTL_CHOICES[ttl],
                created_by=created_by,
                note=note,
            )
        else:
            rule.level = level
            rule.expires_at = datetime.now() + TTL_CHOICES[ttl]
            rule.created_by = created_by
            rule.note = note
        session.add(rule)
        session.commit()
        session.refresh(rule)
    _effective_level_cache.clear()
    return rule


def delete_rule(engine, owner_email: str, source: Optional[str] = None) -> int:
    """Delete rules for a user; restrict to one source when given. Returns
    the number of rules removed."""
    with Session(engine) as session:
        query = select(UserMonitoringRule).where(
            UserMonitoringRule.owner_email == owner_email
        )
        if source is not None:
            query = query.where(UserMonitoringRule.source == source)
        rules = session.exec(query).all()
        for rule in rules:
            session.delete(rule)
        session.commit()
    _effective_level_cache.clear()
    return len(rules)


def list_rules(engine, include_expired: bool = False) -> list[dict]:
    now = datetime.now()
    with Session(engine) as session:
        rules = session.exec(
            select(UserMonitoringRule).order_by(UserMonitoringRule.owner_email)
        ).all()
    out = []
    for r in rules:
        expired = r.expires_at <= now
        if expired and not include_expired:
            continue
        out.append({**r.model_dump(), "expired": expired})
    return out


def get_rules_for(engine, owner_email: str, active_only: bool = True) -> list[dict]:
    now = datetime.now()
    with Session(engine) as session:
        rules = session.exec(
            select(UserMonitoringRule).where(
                UserMonitoringRule.owner_email == owner_email
            )
        ).all()
    out = []
    for r in rules:
        expired = r.expires_at <= now
        if expired and active_only:
            continue
        out.append({**r.model_dump(), "expired": expired})
    return out


def get_effective_level(engine, owner_email: str) -> Optional[str]:
    """Highest active level for this user, or None. Cached ~30s."""
    cache_key = (owner_email, get_ttl_hash(30))
    if cache_key in _effective_level_cache:
        return _effective_level_cache[cache_key]

    now = datetime.now()
    with Session(engine) as session:
        rules = session.exec(
            select(UserMonitoringRule)
            .where(UserMonitoringRule.owner_email == owner_email)
            .where(UserMonitoringRule.expires_at > now)
        ).all()
    level = None
    if rules:
        level = max((r.level for r in rules), key=lambda lv: LEVEL_RANK[lv])

    if len(_effective_level_cache) > 10000:
        _effective_level_cache.clear()
    _effective_level_cache[cache_key] = level
    return level


def resolve_owner_email(engine, api_key: str) -> Optional[str]:
    """API key -> owner_email, cached ~60s (keys are long-lived)."""
    cache_key = (api_key, get_ttl_hash(60))
    if cache_key in _owner_email_cache:
        return _owner_email_cache[cache_key]

    with Session(engine) as session:
        row = session.exec(select(APIKey).where(APIKey.key == api_key)).first()
    email = row.owner_email if row and row.owner_email else None

    if len(_owner_email_cache) > 10000:
        _owner_email_cache.clear()
    _owner_email_cache[cache_key] = email
    return email


def resolve_trace_level(engine, owner_email: str) -> tuple[str, bool]:
    """What to record for this user's request: (level, is_default).

    Everyone is traced at 'metadata' (content-free: model/usage/latency) —
    baseline per-user usage accounting, not optional. An explicit active
    rule overrides the default, typically escalating to 'full'.
    """
    level = get_effective_level(engine, owner_email)
    if level is not None:
        return (level, False)
    return ("metadata", True)


def is_admin(
    engine, api_key: Optional[str] = None, email: Optional[str] = None
) -> bool:
    """Whether the caller is an admin (apikey.is_admin), looked up by their
    API key or by email. Deliberately uncached: admin checks are rare (admin
    endpoints only) and revocation should take effect immediately."""
    with Session(engine) as session:
        if api_key is not None:
            row = session.exec(select(APIKey).where(APIKey.key == api_key)).first()
            if row is not None:
                return bool(row.is_admin)
        if email is not None:
            row = session.exec(
                select(APIKey).where(APIKey.owner_email == email)
            ).first()
            if row is not None:
                return bool(row.is_admin)
    return False
