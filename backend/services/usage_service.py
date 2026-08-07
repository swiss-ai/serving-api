"""Per-user token accounting.

Every request contributes; the numbers are small integers kept forever. This
is deliberately separate from Langfuse tracing, which records prompts and
completions for a named user over a bounded period: at expected traffic,
routing accounting through Langfuse costs ~11 KB of blob storage per request
to record four integers (>1 TB/day), so the two now use different stores.

Writes are aggregated in-process and flushed as UPSERTs, the same shape
MetricsCollector uses for perf_benchmark: a burst of a million calls from one
user against one model collapses into a single row. The buffer is per uvicorn
worker, so several may flush the same key — the UPSERT adds rather than
replaces, so they merge correctly.

Known limit: an unflushed buffer is lost when a pod restarts, which with
`maxSurge: 0` deploys happens regularly. Moving the accumulator to Redis
fixes that and lets pods share one counter; the table and read paths do not
change when we do. See docs 07-usage-admin.
"""

import logging
import threading
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy import func, select as sa_select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import Session

from backend.config import get_settings
from backend.models.entities import UsageDaily

logger = logging.getLogger(__name__)

# Distinct (day, user, model) keys buffered before a flush is triggered. Low
# enough that a quiet instance still persists promptly, high enough that a
# busy one is not writing per request.
FLUSH_THRESHOLD = 25

_buffer: dict[tuple[date, str, str], list[int]] = defaultdict(lambda: [0, 0, 0])
_buffer_lock = threading.Lock()
_engine = None


def _get_engine():
    """Lazy: this module is imported long before settings/DB are ready."""
    global _engine
    if _engine is None:
        from sqlmodel import create_engine

        settings = get_settings()
        if not settings.database_url:
            return None
        _engine = create_engine(settings.database_url, pool_pre_ping=True)
    return _engine


def record_usage(
    owner_email: str,
    model: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    engine=None,
) -> None:
    """Count one request. Never raises — accounting must not break serving."""
    try:
        if not owner_email or not model:
            return
        key = (date.today(), owner_email, model)
        with _buffer_lock:
            entry = _buffer[key]
            entry[0] += 1
            entry[1] += int(prompt_tokens or 0)
            entry[2] += int(completion_tokens or 0)
            due = len(_buffer) >= FLUSH_THRESHOLD
        if due:
            threading.Thread(target=flush, args=(engine,), daemon=True).start()
    except Exception:
        logger.warning("record_usage failed", exc_info=True)


def flush(engine=None) -> int:
    """Persist and clear the buffer. Returns the number of rows written.

    The buffer is swapped out under the lock before any I/O, so requests
    arriving mid-flush accumulate into a fresh buffer instead of being lost
    or double counted.
    """
    global _buffer
    with _buffer_lock:
        if not _buffer:
            return 0
        pending, _buffer = _buffer, defaultdict(lambda: [0, 0, 0])

    engine = engine or _get_engine()
    if engine is None:
        return 0
    now = datetime.now()
    rows = [
        {
            "day": day,
            "owner_email": email,
            "model": model,
            "requests": counts[0],
            "prompt_tokens": counts[1],
            "completion_tokens": counts[2],
            "updated_at": now,
        }
        for (day, email, model), counts in pending.items()
    ]
    try:
        stmt = pg_insert(UsageDaily).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["day", "owner_email", "model"],
            set_={
                "requests": UsageDaily.__table__.c.requests + stmt.excluded.requests,
                "prompt_tokens": UsageDaily.__table__.c.prompt_tokens
                + stmt.excluded.prompt_tokens,
                "completion_tokens": UsageDaily.__table__.c.completion_tokens
                + stmt.excluded.completion_tokens,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        with Session(engine) as session:
            session.exec(stmt)
            session.commit()
        return len(rows)
    except Exception:
        # Put the counts back so the next flush retries them rather than
        # silently dropping a window of usage.
        logger.warning("usage flush failed; re-buffering", exc_info=True)
        with _buffer_lock:
            for (day, email, model), counts in pending.items():
                entry = _buffer[(day, email, model)]
                entry[0] += counts[0]
                entry[1] += counts[1]
                entry[2] += counts[2]
        return 0


def _window_start(days: int) -> date:
    return date.today() - timedelta(days=max(1, days) - 1)


def _totals(engine, days: int, group_col, owner_email: Optional[str] = None):
    """Aggregate the window, grouped by the given column."""
    flush(engine)  # so the caller's own just-made requests are included
    since = _window_start(days)
    query = (
        sa_select(
            group_col,
            func.sum(UsageDaily.requests),
            func.sum(UsageDaily.prompt_tokens),
            func.sum(UsageDaily.completion_tokens),
            func.max(UsageDaily.updated_at),
        )
        .where(UsageDaily.day >= since)
        .group_by(group_col)
    )
    if owner_email is not None:
        query = query.where(UsageDaily.owner_email == owner_email)
    with Session(engine) as session:
        return session.exec(query).all()


def _shape(rows, key_name):
    out = [
        {
            key_name: key,
            "requests": int(reqs or 0),
            "prompt_tokens": int(pin or 0),
            "completion_tokens": int(pout or 0),
            "total_tokens": int(pin or 0) + int(pout or 0),
            "last_active": last.isoformat() if last else None,
        }
        for key, reqs, pin, pout, last in rows
    ]
    out.sort(key=lambda r: -r["requests"])
    return out


def usage_by_user(engine, days: int = 30) -> list[dict]:
    return _shape(_totals(engine, days, UsageDaily.owner_email), "user")


def usage_by_model(engine, days: int = 30) -> list[dict]:
    return _shape(_totals(engine, days, UsageDaily.model), "model")


def usage_for_user(engine, owner_email: str, days: int = 30) -> list[dict]:
    """One user's own usage, broken down by model."""
    return _shape(
        _totals(engine, days, UsageDaily.model, owner_email=owner_email), "model"
    )
