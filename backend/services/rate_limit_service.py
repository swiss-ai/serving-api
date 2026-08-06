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

The effective limit resolves per request, cheapest change wins — no
redeploy needed to adjust:
  1. per-user override   ``rl:limit:<identity>``   (plain integer, admin
     SET/DEL directly in Redis)
  2. global override     ``rl:limit:default``
  3. ``RATE_LIMIT_RPM`` env setting
A resolved limit of 0 (or less) means unlimited at that tier's scope, so
the feature ships dark until an env value or Redis override turns it on.

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

from backend.config import get_settings
from backend.redis_cache import get_token_cache

logger = logging.getLogger(__name__)

WINDOW_SECONDS = 60


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    limit: int = 0
    remaining: int = 0
    retry_after: int = 0


_UNLIMITED = RateLimitDecision(allowed=True)


def _identity(token: str) -> str:
    """Stable per-caller key component. Hashed so raw API keys (secrets)
    never appear as Redis key names."""
    return hashlib.sha256(token.encode()).hexdigest()[:16]


def _effective_limit(user_override, default_override) -> int:
    for value in (user_override, default_override):
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                logger.warning("Ignoring malformed rate limit override %r", value)
    return get_settings().rate_limit_rpm


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

    limit = _effective_limit(user_override, default_override)
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
