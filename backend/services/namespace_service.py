"""Served-name namespacing.

Models launched via SML are served under ``<username>/<vendor>/<model>``,
where the username is the cluster account that submitted the SLURM job —
the same value the job advertises as its ``launched_by`` peer label. The
two must agree: a peer serving "alice/swiss-ai/X" while its job ran as
bob is publishing under someone else's namespace, and we refuse to list
or route it.

The check is lenient wherever there is nothing to compare. Names with
fewer than three segments predate namespacing (or belong to a passthrough
provider) and carry no username; peers that advertise no ``launched_by``
at all run OpenTela <v0.0.6, which emits no labels. Both are left alone,
so every pre-feature launch keeps working.

Two consumers:
- /v1/models* drops squatting peers from the listing (_own_namespace_only
  in backend/routers/models.py).
- Every inference proxy route calls ensure_namespace_ok before proxying.

The model → launcher map is derived from the same DNT table the model
list is built from, cached with a short TTL so a relaunch takes effect
within seconds without making every inference request pay a fetch.
"""

import asyncio
import json
import logging
import pathlib
import time

import aiohttp
from fastapi import HTTPException

from backend.config import get_settings
from backend.services.passthrough_service import resolve_provider

logger = logging.getLogger("backend")

# Short TTL: a relaunch under the right namespace should take effect
# within seconds, while burst traffic for one model still coalesces to a
# single DNT fetch.
_CACHE_TTL_SECONDS = 10.0
# Timeout for the DNT fetch — keep tight so a wedged head node can't
# stall inference requests on our side.
_FETCH_TIMEOUT_SECONDS = 5.0

# A served name with fewer than this many "/"-separated segments predates
# namespacing (a bare "model" or a "vendor/model") — there is no username
# in it to check. Mirrors swiss_ai_model_launch.launchers.served_name.
_NAMESPACED_SEGMENTS = 3

_cache_lock = asyncio.Lock()
# {"fetched_at": float, "launcher_map": dict[str, list[str]] | None}
_cache: dict = {"fetched_at": 0.0, "launcher_map": None}


def _reset_cache_for_tests() -> None:
    """Test helper — clears the cache so tests can simulate cold start
    without leaking state across cases."""
    _cache["fetched_at"] = 0.0
    _cache["launcher_map"] = None


def namespace_of(model_id: str) -> str | None:
    """The username a served name is published under, or None when the name
    carries no namespace (fewer than three segments — a pre-namespacing
    launch, or a passthrough provider's id)."""
    parts = model_id.split("/")
    if len(parts) < _NAMESPACED_SEGMENTS:
        return None
    return parts[0]


def namespace_matches(model_id: str, launched_by: str) -> bool:
    """Does this peer's served name agree with the account that launched it?

    True when the name isn't namespaced (nothing to check) or the peer
    advertises no ``launched_by`` (nothing to check it against — legacy
    OpenTela binaries emit no labels, and refusing them would break every
    such launch). Otherwise the namespace must be the launching account,
    compared case-insensitively and whitespace-tolerantly on both sides."""
    namespace = namespace_of(model_id)
    if namespace is None:
        return True
    launcher = (launched_by or "").strip().lower()
    if not launcher:
        return True
    return namespace.strip().lower() == launcher


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


def _build_launcher_map(data: dict) -> dict[str, list[str]]:
    """model_id → the ``launched_by`` label of every peer entry serving it.

    Mirrors model_service.get_all_models id extraction: service
    identity_group "model=..." entries, plus the labels.served_model_name
    fallback for pending/follower peers. Peers of one launch carry the
    same labels as their head, so they agree; a peer that disagrees
    belongs to a DIFFERENT launch squatting the same name, which is
    exactly what ensure_namespace_ok refuses to route."""
    launcher_map: dict[str, list[str]] = {}
    for node_info in data.values():
        labels = node_info.get("labels") or {}
        launched_by = labels.get("launched_by", "")
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
            launcher_map.setdefault(model_name, []).append(launched_by)
    return launcher_map


async def _get_launcher_map() -> dict[str, list[str]] | None:
    """The cached model → launched_by map. Refreshes past the TTL; on fetch
    failure keeps serving the stale map. Returns None only at true cold
    start (never fetched successfully) — the caller fails open.

    ``fetched_at`` records the last *attempt*, successful or not: retrying
    on every request while the DNT is down would make each inference call
    pay the fetch timeout. One retry per TTL is enough."""
    if (time.time() - _cache["fetched_at"]) < _CACHE_TTL_SECONDS:
        return _cache["launcher_map"]

    async with _cache_lock:
        # Another coroutine may have refreshed while we waited on the lock.
        if (time.time() - _cache["fetched_at"]) < _CACHE_TTL_SECONDS:
            return _cache["launcher_map"]

        data = await _fetch_dnt()
        if data is not None:
            _cache["launcher_map"] = _build_launcher_map(data)
        _cache["fetched_at"] = time.time()
        return _cache["launcher_map"]


async def ensure_namespace_ok(model_id: str) -> None:
    """Raise 403 unless every peer serving ``model_id`` publishes it under
    its own launching account's namespace.

    Allowed when: the model routes to a passthrough provider (never
    namespaced), the id is unknown to the DNT (falls through to upstream
    which 404s — unchanged behavior), every peer entry's namespace matches
    its ``launched_by``, or the DNT has never been fetchable (fail open,
    logged).

    A peer whose served name is namespaced under someone else's username
    makes the id unroutable for EVERYONE, not just that peer: OpenTela
    load-balances the name across every peer advertising it, so we cannot
    keep a request off the squatting peer.
    """
    if not model_id or not isinstance(model_id, str):
        return
    if await resolve_provider(model_id) is not None:
        return
    launcher_map = await _get_launcher_map()
    if launcher_map is None:
        logger.warning(
            "DNT unreachable with no cached launcher map — "
            "allowing request for model '%s' (fail open)",
            model_id,
        )
        return
    launchers = launcher_map.get(model_id)
    if launchers is None:
        return
    squatters = sorted(
        {
            launched_by
            for launched_by in launchers
            if not namespace_matches(model_id, launched_by)
        }
    )
    if not squatters:
        return
    logger.warning(
        "Refusing model '%s': served by peer(s) launched by %s, outside its namespace",
        model_id,
        ", ".join(repr(s) for s in squatters),
    )
    raise HTTPException(
        status_code=403,
        detail=(
            f"Access denied: model '{model_id}' is served by replicas launched "
            f"outside its '{namespace_of(model_id)}' namespace; requests are "
            f"refused until the conflict is resolved (relaunch under your own "
            f"username's namespace)."
        ),
    )
