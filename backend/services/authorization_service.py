"""Per-user model authorization.

Models launched via SML carry an OpenTela peer label ``authorization``:
"public" (or missing/empty — every pre-feature launch keeps working) means
anyone may use the model; a comma-separated email list restricts it to
those users. SML normalizes emails (strip, lowercase) before launch, but
we compare case-insensitively anyway as defense in depth.

Two consumers:
- /v1/models* filters what each caller sees (grants_access per entry).
- Every inference proxy route calls ensure_model_access before proxying.

The model → authorization map is derived from the same DNT table the
models router reads, cached module-level with a short TTL so the check
adds no upstream round-trip on the hot path. Failure policy: serve stale
cache when the DNT is briefly unreachable; only at true cold start (no
cache at all) do we fail OPEN with a logged warning — an unreachable DNT
must never 500 (or wrongly 403) inference traffic.
"""

import asyncio
import json
import logging
import pathlib
import time

import aiohttp
from fastapi import HTTPException

from backend.config import get_settings
from backend.services.auth_service import get_email_for_token
from backend.services.passthrough_service import resolve_provider

logger = logging.getLogger("backend")

# Short TTL: a permission change on relaunch should take effect within
# seconds, while burst traffic for one model still coalesces to a single
# DNT fetch.
_CACHE_TTL_SECONDS = 10.0
# Timeout for the DNT fetch — keep tight so a wedged head node can't
# stall inference requests on our side.
_FETCH_TIMEOUT_SECONDS = 5.0

_cache_lock = asyncio.Lock()
# {"fetched_at": float, "auth_map": dict[str, list[str]] | None}
_cache: dict = {"fetched_at": 0.0, "auth_map": None}


def _reset_cache_for_tests() -> None:
    """Test helper — clears the cache so tests can simulate cold start
    without leaking state across cases."""
    _cache["fetched_at"] = 0.0
    _cache["auth_map"] = None


def grants_access(auth_value: str, email: str | None) -> bool:
    """Does one entry's ``authorization`` label value admit this caller?

    Empty/missing or "public" → anyone (incl. anonymous). Otherwise the
    value is a comma-separated email list; only a matching caller passes.
    Comparison is case-insensitive on both sides — SML normalizes before
    launch, this is defense in depth.
    """
    value = (auth_value or "").strip()
    if not value or value.lower() == "public":
        return True
    if email is None:
        return False
    allowed = {part.strip().lower() for part in value.split(",")}
    return email.strip().lower() in allowed


def _dnt_endpoint() -> str:
    """When OTELA_FIXTURE_PATH is set, read DNT from disk instead of HTTP —
    same fixture-mode dev path as backend/routers/models.py."""
    settings = get_settings()
    if settings.otela_fixture_path:
        return settings.otela_fixture_path
    return settings.otela_head_addr + "/v1/dnt/table"


async def _fetch_dnt() -> dict | None:
    """Fetch the DNT table (file in fixture mode, HTTP otherwise). Returns
    None on any failure so the caller can decide between stale cache and
    fail-open."""
    endpoint = _dnt_endpoint()
    try:
        if endpoint and not endpoint.startswith(("http://", "https://")):
            return json.loads(pathlib.Path(endpoint).read_text())
        timeout = aiohttp.ClientTimeout(total=_FETCH_TIMEOUT_SECONDS)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(endpoint) as resp:
                if resp.status != 200:
                    return None
                # content_type=None: parse regardless of the Content-Type
                # header, matching the leniency of the other consumers of
                # this endpoint (model_service._load_dnt via requests). A
                # strict parse here would silently pin enforcement in the
                # cold-start fail-open branch while listing keeps working.
                return await resp.json(content_type=None)
    except Exception:
        return None


def _build_auth_map(data: dict) -> dict[str, list[str]]:
    """model_id → the ``authorization`` label value of every peer entry
    serving it. Mirrors model_service.get_all_models id extraction: service
    identity_group "model=..." entries, plus the labels.served_model_name
    fallback for pending/follower peers (they carry the same labels as
    their head, so including them can only re-state an existing grant)."""
    auth_map: dict[str, list[str]] = {}
    for node_info in data.values():
        labels = node_info.get("labels") or {}
        auth_value = labels.get("authorization", "")
        model_names = []
        services = node_info.get("service") or []
        if not services:
            served = labels.get("served_model_name", "")
            if served:
                model_names.append(served)
        for service in services:
            if not service.get("identity_group"):
                continue
            model_names.extend(
                identity[len("model=") :]
                for identity in service["identity_group"]
                if identity.startswith("model=")
            )
        for model_name in model_names:
            auth_map.setdefault(model_name, []).append(auth_value)
    return auth_map


async def _get_auth_map() -> dict[str, list[str]] | None:
    """The cached model → authorization-values map. Refreshes past the TTL;
    on fetch failure keeps serving the stale map. Returns None only at true
    cold start (never fetched successfully) — the caller fails open.

    ``fetched_at`` records the last *attempt*, successful or not: retrying
    on every request while the DNT is down would make each inference call
    pay the fetch timeout. One retry per TTL is enough."""
    if (time.time() - _cache["fetched_at"]) < _CACHE_TTL_SECONDS:
        return _cache["auth_map"]

    async with _cache_lock:
        # Another coroutine may have refreshed while we waited on the lock.
        if (time.time() - _cache["fetched_at"]) < _CACHE_TTL_SECONDS:
            return _cache["auth_map"]

        data = await _fetch_dnt()
        if data is not None:
            _cache["auth_map"] = _build_auth_map(data)
        _cache["fetched_at"] = time.time()
        return _cache["auth_map"]


async def ensure_model_access(engine, token: str, model_id: str) -> None:
    """Raise 403 unless the API key's owner may use ``model_id``.

    Allowed when: the model routes to a passthrough provider (always
    public), the id is unknown to the DNT (falls through to upstream which
    404s — unchanged behavior), ANY of its peer entries grants access, or
    the DNT has never been fetchable (fail open, logged)."""
    if not isinstance(model_id, str):
        # Several routes pass the raw body value unvalidated; a non-string
        # id can't be looked up (unhashable) — treat it like an unknown
        # model and let the upstream reject it with its own 4xx.
        return
    if await resolve_provider(model_id) is not None:
        return
    auth_map = await _get_auth_map()
    if auth_map is None:
        logger.warning(
            "DNT unreachable with no cached authorization map — "
            "allowing request for model '%s' (fail open)",
            model_id,
        )
        return
    auth_values = auth_map.get(model_id)
    if auth_values is None:
        return
    email = get_email_for_token(engine, token)
    if any(grants_access(value, email) for value in auth_values):
        return
    raise HTTPException(
        status_code=403,
        detail=f"Access denied: you are not authorized to use model '{model_id}'.",
    )
