"""Per-user model authorization and served-name namespacing.

Models launched via SML carry an OpenTela peer label ``authorization``:
"public" (or missing/empty — every pre-feature launch keeps working) means
anyone may use the model; a comma-separated email list restricts it to
those users. SML normalizes emails (strip, lowercase) before launch, but
we compare case-insensitively anyway as defense in depth.

SML also namespaces every served name as ``<username>/<vendor>/<model>``,
where the username is the cluster account that submitted the SLURM job —
the same value the job advertises as its ``launched_by`` label. The two
must agree: a peer serving "alice/swiss-ai/X" while its job ran as bob is
publishing under someone else's namespace, and we refuse to list or route
it. Names with fewer than three segments predate namespacing and are left
unchecked, as are peers that advertise no ``launched_by`` at all (OpenTela
<v0.0.6 emits no labels — there is nothing to compare against).

Two consumers:
- /v1/models* filters what each caller sees (grants_access + namespace).
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
from typing import NamedTuple

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
# {"fetched_at": float, "model_map": dict[str, list[PeerClaim]] | None}
_cache: dict = {"fetched_at": 0.0, "model_map": None}

# A served name with fewer than this many "/"-separated segments predates
# namespacing (a bare "model" or a "vendor/model") — there is no username
# in it to check. Mirrors swiss_ai_model_launch.launchers.served_name.
_NAMESPACED_SEGMENTS = 3


class PeerClaim(NamedTuple):
    """What one peer entry claims about a model id it serves: the policy it
    publishes, and the account its job ran as."""

    authorization: str
    launched_by: str


def _reset_cache_for_tests() -> None:
    """Test helper — clears the cache so tests can simulate cold start
    without leaking state across cases."""
    _cache["fetched_at"] = 0.0
    _cache["model_map"] = None


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
    compared case-insensitively like every other label here."""
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


def _build_model_map(data: dict) -> dict[str, list[PeerClaim]]:
    """model_id → one PeerClaim per peer entry serving it. Mirrors
    model_service.get_all_models id extraction: service identity_group
    "model=..." entries, plus the labels.served_model_name fallback for
    pending/follower peers. Peers of one launch carry the same labels as
    their head, so they normalize to one policy; a peer that disagrees
    belongs to a DIFFERENT launch squatting the same name, which is
    exactly the conflict ensure_model_access refuses to route."""
    model_map: dict[str, list[PeerClaim]] = {}
    for node_info in data.values():
        labels = node_info.get("labels") or {}
        claim = PeerClaim(
            authorization=labels.get("authorization", ""),
            launched_by=labels.get("launched_by", ""),
        )
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
            model_map.setdefault(model_name, []).append(claim)
    return model_map


async def _get_model_map() -> dict[str, list[PeerClaim]] | None:
    """The cached model → peer-claims map. Refreshes past the TTL; on fetch
    failure keeps serving the stale map. Returns None only at true cold
    start (never fetched successfully) — the caller fails open.

    ``fetched_at`` records the last *attempt*, successful or not: retrying
    on every request while the DNT is down would make each inference call
    pay the fetch timeout. One retry per TTL is enough."""
    if (time.time() - _cache["fetched_at"]) < _CACHE_TTL_SECONDS:
        return _cache["model_map"]

    async with _cache_lock:
        # Another coroutine may have refreshed while we waited on the lock.
        if (time.time() - _cache["fetched_at"]) < _CACHE_TTL_SECONDS:
            return _cache["model_map"]

        data = await _fetch_dnt()
        if data is not None:
            _cache["model_map"] = _build_model_map(data)
        _cache["fetched_at"] = time.time()
        return _cache["model_map"]


async def ensure_model_access(engine, token: str, model_id: str) -> None:
    """Raise 403 unless the API key's owner may use ``model_id``.

    Allowed when: the model routes to a passthrough provider (always
    public), the id is unknown to the DNT (falls through to upstream which
    404s — unchanged behavior), every peer entry serves it under a
    namespace matching its own launching account and agrees on one policy
    that grants the caller, or the DNT has never been fetchable (fail
    open, logged).

    A peer whose served name is namespaced under someone else's username
    is refused for EVERYONE serving that id, for the same reason a policy
    conflict is: OpenTela load-balances the name across every peer
    advertising it, so we cannot keep a request off the squatting peer.

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
    if await resolve_provider(model_id) is not None:
        return
    model_map = await _get_model_map()
    if model_map is None:
        logger.warning(
            "DNT unreachable with no cached authorization map — "
            "allowing request for model '%s' (fail open)",
            model_id,
        )
        return
    claims = model_map.get(model_id)
    if claims is None:
        return
    squatters = sorted(
        {
            c.launched_by
            for c in claims
            if not namespace_matches(model_id, c.launched_by)
        }
    )
    if squatters:
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
    policies = {normalize_policy(c.authorization) for c in claims}
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
