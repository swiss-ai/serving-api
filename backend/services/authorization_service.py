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
from backend.services.passthrough_service import resolve_model

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


def normalize_policy(auth_value: str) -> frozenset[str] | None:
    """One entry's ``authorization`` label value as a canonical policy.

    None means public (missing/empty label or "public" in any case);
    otherwise the granted email set, lowercased and stripped. Two label
    strings that differ only in order, case, or spacing normalize to the
    SAME policy — which is what conflict detection compares, so a
    relaunch that reorders its list is not a conflict.
    """
    value = (auth_value or "").strip()
    if not value or value.lower() == "public":
        return None
    return frozenset(p.strip().lower() for p in value.split(",") if p.strip())


def grants_access(auth_value: str, email: str | None) -> bool:
    """Does one entry's ``authorization`` label value admit this caller?

    Public policy → anyone (incl. anonymous). Otherwise only a listed
    caller passes. Comparison is case-insensitive on both sides — SML
    normalizes before launch, this is defense in depth.
    """
    policy = normalize_policy(auth_value)
    if policy is None:
        return True
    if email is None:
        return False
    return email.strip().lower() in policy


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
    fallback for pending/follower peers. Peers of one launch carry the
    same labels as their head, so they normalize to one policy; a peer
    that disagrees belongs to a DIFFERENT launch squatting the same name,
    which is exactly the conflict ensure_model_access refuses to route."""
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


async def _dnt_keys_for(model_id: str) -> list[str] | None:
    """The DNT ids a requested model id may be advertised under, or None
    when it belongs to a passthrough provider (always public).

    ``SwissAI-Research/<org>/<model>`` is this platform's public alias for
    a model on our own OpenTela network, and the proxy strips the prefix
    before forwarding (see passthrough_service.resolve_model). Peers may
    therefore be advertising either form, so both are checked and their
    labels pooled: if the two forms carry *different* policies they are
    independent launches reachable under one routed name, which is exactly
    the collision the conflict rule below refuses.

    Every other id — a user launch (``<username>/<org>/<model>``) or a
    pre-namespacing bare name — is forwarded unchanged, so it is its own
    only key."""
    resolved = await resolve_model(model_id)
    if resolved is None:
        return [model_id]
    if resolved.provider is not None:
        return None
    return [model_id, resolved.upstream_id]


async def ensure_model_access(engine, token: str, model_id: str) -> None:
    """Raise 403 unless the API key's owner may use ``model_id``.

    Allowed when: the model routes to a passthrough provider (always
    public), the id is unknown to the DNT (falls through to upstream which
    404s — unchanged behavior), every peer entry agrees on one policy and
    that policy grants the caller, or the DNT has never been fetchable
    (fail open, logged).

    Entries that disagree (after normalization) are an authorization
    CONFLICT and everyone is refused — even a caller granted by all of
    the policies. OpenTela load-balances a model id across every peer
    advertising it, so on a name collision the gateway cannot keep a
    request off the colliding launcher's replica; the union rule would
    let a same-named public launch widen access to a restricted model,
    and any allow at all would route callers' prompts to a replica they
    never chose to trust. Refusing loudly turns a collision into a
    visible operational error instead of a silent leak."""
    if not isinstance(model_id, str):
        # Several routes pass the raw body value unvalidated; a non-string
        # id can't be looked up (unhashable) — treat it like an unknown
        # model and let the upstream reject it with its own 4xx.
        return
    keys = await _dnt_keys_for(model_id)
    if keys is None:
        return
    auth_map = await _get_auth_map()
    if auth_map is None:
        logger.warning(
            "DNT unreachable with no cached authorization map — "
            "allowing request for model '%s' (fail open)",
            model_id,
        )
        return
    auth_values = [value for key in keys for value in auth_map.get(key, [])]
    if not auth_values:
        return
    policies = {normalize_policy(value) for value in auth_values}
    if len(policies) > 1:
        raise HTTPException(
            status_code=403,
            detail=(
                f"Access denied: model '{model_id}' is served by replicas with "
                f"conflicting authorization labels; requests are refused until "
                f"the conflict is resolved (relaunch under a unique "
                f"--served-model-name or with a matching --authorization)."
            ),
        )
    (policy,) = policies
    if policy is None:
        return
    email = get_email_for_token(engine, token)
    if email is not None and email.strip().lower() in policy:
        return
    raise HTTPException(
        status_code=403,
        detail=f"Access denied: you are not authorized to use model '{model_id}'.",
    )
