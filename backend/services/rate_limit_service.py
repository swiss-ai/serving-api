"""Per-user request rate limiting for external passthrough providers.

Only requests that route to an external provider (CSCS L1, RCP, ...) are
counted and limited: those consume a shared, platform-accountable
resource (shared upstream API key, external quota). OpenTela-served
models run on the caller's own GPU allocation and are deliberately not
limited. Enforcement sits on the passthrough arm of model routing — see
``enforce_rate_limit`` in backend/middleware/ratelimit.py.

Counters live in Redis so one limit holds across every gateway replica —
per-replica in-memory counting would let clients multiply their budget by
whatever the load balancer distributes over. The sliding-window
approximation weighs the previous minute's count by its remaining overlap
with the last 60 seconds, which smooths the burst a plain fixed window
would admit at each boundary.

The effective limit resolves per request, first tier that sets one wins
— no redeploy needed to adjust:
  1. per-user override   ``rl:limit:<identity>``   (plain integer, admin
     SET/DEL directly in Redis) — the instant hotfix
  2. per-user limit      ``apikey.rate_limit_rpm`` (set via
     PUT /v1/admin/rate-limits/{email}) — the durable one
  3. global override     ``rl:limit:default``
  4. ``RATE_LIMIT_RPM`` env setting
A resolved limit of 0 (or less) means unlimited at that tier's scope, so
the feature ships dark until an env value or Redis override turns it on.

Grant lasting per-user limits through tier 2, not tier 1. Prod Redis is
an LRU cache on an emptyDir, so a Redis override can be evicted or lost
on restart, and it is keyed by a hash of the exact API key, so it goes
stale when the user rotates their key and is easy to write under the
wrong hash. The DB limit lives on the user's apikey row, which
rotate_key rewrites in place.

The chat UIs' keys (``CHAT_API_KEYS``) skip all of the above. Open WebUI
reaches us through one shared connection key, so a per-key limit would
throttle every chat user together; chat is never limited. Those requests
touch no counters.

Availability beats strictness: Redis being unreachable logs a warning and
allows the request. Rejected requests still count toward the window, so a
client hammering through 429s stays limited instead of resetting its own
budget.
"""

import hashlib
import logging
import math
import time
from dataclasses import dataclass

from sqlmodel import Session, select

from backend.config import get_settings
from backend.models.entities import APIKey
from backend.redis_cache import get_token_cache
from backend.services.metrics_service import get_ttl_hash
from backend.services.usage_service import _get_engine

logger = logging.getLogger(__name__)

WINDOW_SECONDS = 60


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    limit: int = 0
    remaining: int = 0
    retry_after: int = 0


_UNLIMITED = RateLimitDecision(allowed=True)

_db_limit_cache: dict = {}


def _identity(token: str) -> str:
    """Stable per-caller key component. Hashed so raw API keys (secrets)
    never appear as Redis key names."""
    return hashlib.sha256(token.encode()).hexdigest()[:16]


def is_exempt(token: str) -> bool:
    """Whether this is one of the chat UIs' keys (CHAT_API_KEYS)."""
    return token in {
        k.strip() for k in get_settings().chat_api_keys.split(",") if k.strip()
    }


def _db_limit(token: str) -> int | None:
    """This key's apikey.rate_limit_rpm. Cached ~60s per process, like
    resolve_owner_email, so the steady-state hot path costs no DB query;
    a change takes effect on every replica within a minute. No DB, or a
    failed lookup, means no per-user limit — the defaults still apply."""
    engine = _get_engine()
    if engine is None:
        return None
    cache_key = (token, get_ttl_hash(60))
    if cache_key in _db_limit_cache:
        return _db_limit_cache[cache_key]
    try:
        with Session(engine) as session:
            limit = session.exec(
                select(APIKey.rate_limit_rpm).where(APIKey.key == token)
            ).first()
    except Exception:
        logger.warning("Per-user rate limit lookup failed", exc_info=True)
        return None

    if len(_db_limit_cache) > 10000:
        _db_limit_cache.clear()
    _db_limit_cache[cache_key] = limit
    return limit


def _parse_override(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        logger.warning("Ignoring malformed rate limit override %r", value)
        return None


def _effective_limit(token: str, user_override, default_override) -> int:
    """First tier that sets a limit wins (module docstring). The DB is only
    consulted when no Redis per-user override is set."""
    limit = _parse_override(user_override)
    if limit is None:
        limit = _db_limit(token)
    if limit is None:
        limit = _parse_override(default_override)
    if limit is None:
        limit = get_settings().rate_limit_rpm
    return limit


def set_user_limit(engine, owner_email: str, rpm: int | None) -> int:
    """Set the durable per-user limit on every key the user owns, or clear
    it with None (back to the defaults). Returns how many keys were
    updated; 0 means there is no such user. Other replicas pick the change
    up within a minute (_db_limit's cache)."""
    with Session(engine) as session:
        rows = session.exec(
            select(APIKey).where(APIKey.owner_email == owner_email)
        ).all()
        for row in rows:
            row.rate_limit_rpm = rpm
            session.add(row)
        session.commit()
    _db_limit_cache.clear()
    return len(rows)


def list_user_limits(engine) -> list[dict]:
    """Users with a durable per-user limit. Redis rl:limit:<identity>
    overrides are not included: they are keyed by key hash, not email."""
    with Session(engine) as session:
        rows = session.exec(
            select(APIKey.owner_email, APIKey.rate_limit_rpm)
            .where(APIKey.rate_limit_rpm.is_not(None))
            .order_by(APIKey.owner_email)
        ).all()
    return [{"owner_email": email, "rate_limit_rpm": rpm} for email, rpm in rows]


def check_rate_limit(token: str, now: float | None = None) -> RateLimitDecision:
    """Count this request against the caller's sliding window and decide.

    Always increments before deciding (even when the verdict is 429, see
    module docstring) — one pipelined round trip covers the counter update
    and both override lookups.
    """
    client = get_token_cache().redis_client
    if client is None:
        return _UNLIMITED
    if now is None:
        now = time.time()
    ident = _identity(token)
    window = int(now // WINDOW_SECONDS)
    try:
        pipe = client.pipeline()
        pipe.incr(f"rl:req:{ident}:{window}")
        # Two windows: the key must survive long enough to serve as the
        # "previous" bucket for the whole of the next window.
        pipe.expire(f"rl:req:{ident}:{window}", WINDOW_SECONDS * 2)
        pipe.get(f"rl:req:{ident}:{window - 1}")
        pipe.get(f"rl:limit:{ident}")
        pipe.get("rl:limit:default")
        current, _, previous, user_override, default_override = pipe.execute()
    except Exception:
        logger.warning(
            "Rate limiter Redis unavailable; allowing request", exc_info=True
        )
        return _UNLIMITED

    limit = _effective_limit(token, user_override, default_override)
    if limit <= 0:
        return _UNLIMITED

    elapsed = now - window * WINDOW_SECONDS
    prev_weight = (WINDOW_SECONDS - elapsed) / WINDOW_SECONDS
    weighted = int(previous or 0) * prev_weight + int(current)
    if weighted > limit:
        return RateLimitDecision(
            allowed=False,
            limit=limit,
            remaining=0,
            retry_after=max(1, math.ceil(WINDOW_SECONDS - elapsed)),
        )
    return RateLimitDecision(
        allowed=True,
        limit=limit,
        remaining=max(0, int(limit - weighted)),
        retry_after=0,
    )
