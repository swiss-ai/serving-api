"""OpenAI-compatible passthrough providers.

Some upstreams (CSCS L1, EPFL RCP, ...) already serve OpenAI-compatible
models. Rather than launch duplicate pods for them ourselves, we forward
requests for their model ids straight through and surface those ids in
/v1/models* alongside our locally-served (OpenTela) models.

Each provider is a small config: a base URL + API key (from env via
Settings), a display label, and an optional cold-start fallback id list.
Adding a provider = one entry in `registered_providers()`, not new code.

Discovery: we hit each provider's own /models endpoint on first use (and
every 30 s thereafter) so the set of routable models tracks whatever the
upstream exposes, without code changes. A per-provider `fallback_ids`
list backstops the cold-start case when the upstream is unreachable on
the very first fetch, so its rows aren't completely missing during a
brief outage.

Every id a provider surfaces is namespaced under its reserved prefix
(``CSCS-Inference/...``, ``RCP-AIaaS/...``), so ids are structurally
collision-free across providers and against OpenTela-served models: the
first path segment of a requested id selects the provider, the remainder
is forwarded verbatim as the upstream id (see ``resolve_model``).
Un-prefixed upstream ids still route during a deprecation window, where
registration order is precedence.

Curation is per provider via ``Provider.allowed_ids``: RCP advertises
far more than we want to expose (~26 models, incl. quant variants), so
its discovered set is narrowed to an allowlist before it is listed or
routed; CSCS L1 is currently unrestricted — whatever it advertises
surfaces. Allowlist matches are exact — an id under a different org
prefix is not surfaced.

Secrets (base URLs, API keys) come from env via Settings.
"""

import asyncio
import logging
import time
from dataclasses import dataclass

import aiohttp

from backend.config import get_settings

logger = logging.getLogger(__name__)


# 30 s strikes a balance: short enough that a new upstream model is
# visible within half a minute, long enough that page reloads +
# completion dispatches don't hammer the upstream.
_CACHE_TTL_SECONDS = 30.0
# Timeout for the GET /models probe — keep tight so a wedged upstream
# can't stall /v1/models page loads on our side.
_FETCH_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class Provider:
    """A single OpenAI-compatible passthrough upstream.

    `name` doubles as the synthetic entry's ``launched_by`` value, so the
    frontend can tag the model card as a passthrough (see the
    PASSTHROUGH_LAUNCHERS set in frontend/src/lib/modelMetrics.ts).
    """

    name: str
    base_url: str
    api_key: str
    device: str
    # Namespace prefix carried by every id this provider surfaces
    # (``<prefix>/<upstream id>``). The first path segment of a requested
    # model id is matched against these, so prefixes are reserved names:
    # they must never collide with a HF org, a username, or each other.
    prefix: str = ""
    # Cold-start backstop, used only if we haven't successfully fetched
    # /models yet AND the current fetch fails. Empty = nothing advertised
    # until the first successful fetch.
    fallback_ids: tuple[str, ...] = ()
    # Curation allowlist: only these exact ids are surfaced (listed or
    # routed) from this provider. None = unrestricted — everything the
    # upstream advertises surfaces. Match is exact and verbatim — an id
    # under a different org prefix (e.g. bare `Apertus-8B-Instruct-2509`)
    # does NOT match.
    allowed_ids: frozenset[str] | None = None


# RCP advertises far more than we want to expose (~26 models, incl. quant
# variants), so it is narrowed to just the Apertus pair it backs up.
_RCP_ALLOWED_MODEL_IDS: frozenset[str] = frozenset(
    {
        "swiss-ai/Apertus-8B-Instruct-2509",
        "swiss-ai/Apertus-70B-Instruct-2509",
    }
)


# CSCS L1 cold-start fallback so the Apertus rows don't vanish during a
# brief L1 outage on the very first fetch. New providers (e.g. RCP) get
# no fallback — we just wait for the first successful discovery.
_CSCS_L1_FALLBACK_IDS: tuple[str, ...] = (
    "swiss-ai/Apertus-70B-Instruct-2509",
    "swiss-ai/Apertus-8B-Instruct-2509",
)


def registered_providers() -> list[Provider]:
    """Build the provider list from env. Only fully-configured providers
    (both base URL and API key) are included — a half-configured provider
    is skipped so we never try to proxy to an empty URL. Order here is
    routing/listing precedence on id collisions."""
    s = get_settings()
    providers: list[Provider] = []
    if s.cscs_l1_base_url and s.cscs_l1_api_key:
        providers.append(
            Provider(
                name="cscs_L1",
                base_url=s.cscs_l1_base_url,
                api_key=s.cscs_l1_api_key,
                device="CSCS L1",
                prefix="CSCS-Inference",
                fallback_ids=_CSCS_L1_FALLBACK_IDS,
            )
        )
    if s.rcp_base_url and s.rcp_api_key:
        providers.append(
            Provider(
                name="rcp",
                base_url=s.rcp_base_url,
                api_key=s.rcp_api_key,
                device="EPFL RCP",
                prefix="RCP-AIaaS",
                allowed_ids=_RCP_ALLOWED_MODEL_IDS,
            )
        )
    return providers


_cache_lock = asyncio.Lock()
# Keyed by provider name → {"fetched_at": float, "ids": set | None}.
_cache: dict[str, dict] = {}


def _reset_cache_for_tests() -> None:
    """Test helper — clears the cache so tests can simulate cold start
    without leaking state across cases."""
    _cache.clear()


def endpoint(provider: Provider) -> str:
    """Base URL for the provider's OpenAI-compatible API (e.g.
    https://.../v1). Callers append /chat/completions etc., so strip a
    trailing slash defensively to avoid a double-slash URL."""
    return provider.base_url.rstrip("/")


async def _fetch_model_ids(provider: Provider) -> set[str] | None:
    """GET {base}/models from the provider. Returns None on any failure
    (network, non-200, malformed JSON) so the caller can decide whether
    to keep stale cache or fall back."""
    url = endpoint(provider) + "/models"
    headers = {"Authorization": f"Bearer {provider.api_key}"}
    try:
        timeout = aiohttp.ClientTimeout(total=_FETCH_TIMEOUT_SECONDS)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
        return {m["id"] for m in data.get("data", []) if m.get("id")}
    except Exception:
        return None


async def _get_cached_ids(provider: Provider) -> set[str]:
    """The provider's *advertised* id set: whatever discovery currently
    knows, narrowed to the provider's ``allowed_ids`` (if any). This is the
    single boundary both listing (``get_synthetic_entries``) and routing
    (``resolve_provider``) go through, so the same allowlist governs both —
    a model we don't list also can't be routed to."""
    discovered = await _discover_ids(provider)
    if provider.allowed_ids is None:
        return discovered
    return provider.allowed_ids & discovered


async def _discover_ids(provider: Provider) -> set[str]:
    """Return the provider's discovered model id set (pre-allowlist).
    Refreshes if TTL has expired; on fetch failure keeps stale cache,
    falling back to ``provider.fallback_ids`` only at true cold start. A
    transient upstream outage shouldn't make rows that *were* there
    disappear."""
    now = time.time()
    slot = _cache.get(provider.name)
    if (
        slot is not None
        and slot["ids"] is not None
        and (now - slot["fetched_at"]) < _CACHE_TTL_SECONDS
    ):
        return slot["ids"]

    async with _cache_lock:
        # Another coroutine may have refreshed while we waited on the lock.
        slot = _cache.get(provider.name)
        if (
            slot is not None
            and slot["ids"] is not None
            and (time.time() - slot["fetched_at"]) < _CACHE_TTL_SECONDS
        ):
            return slot["ids"]

        fetched = await _fetch_model_ids(provider)
        if fetched is not None:
            _cache[provider.name] = {"ids": fetched, "fetched_at": time.time()}
            return fetched

        if slot is not None and slot["ids"] is not None:
            # Keep serving stale cache; don't touch fetched_at so we retry
            # on the next call instead of waiting a full TTL.
            return slot["ids"]

        return set(provider.fallback_ids)


# This platform's own namespace: SwissAIResearch/<org>/<model> is the
# canonical alias for a model served by us (OpenTela). Reserved alongside
# the provider prefixes — no HF org, username, or provider may claim it.
PLATFORM_PREFIX = "SwissAIResearch"


@dataclass(frozen=True)
class ResolvedModel:
    """A model id resolved through the namespace registry.

    ``provider`` is the passthrough upstream serving it, or None when the
    id is under ``PLATFORM_PREFIX`` (served by our own OpenTela network).
    ``upstream_id`` is what the serving side knows the model as (goes in
    the forwarded request body); ``public_id`` is the prefixed form we
    expose (rewritten back into responses so clients see the id they
    asked for).
    """

    provider: Provider | None
    upstream_id: str
    public_id: str


async def resolve_model(model_id: str) -> ResolvedModel | None:
    """Resolve a requested model id through the namespace registry, or
    None so the caller falls through to OpenTela (which 404s cleanly).

    Namespaces, selected by the first path segment:
    - ``SwissAIResearch/<org>/<model>`` — our own OpenTela-served models
      (provider=None). No advertised-set check: OpenTela 404s unknown ids
      itself.
    - ``<provider prefix>/<upstream id>`` (CSCS-Inference/...,
      RCP-AIaaS/...) — the remainder must be an id the provider currently
      advertises. A prefixed id whose remainder is unknown does NOT fall
      through to OpenTela by another name; it returns None and 404s there
      under its full (never-launched) id.

    Back-compat: a bare upstream id that a provider advertises still routes
    (first provider in registration order wins), logged as deprecated.
    Remove after clients have migrated to prefixed ids."""
    if not model_id:
        return None
    providers = registered_providers()
    first, _, rest = model_id.partition("/")
    if first == PLATFORM_PREFIX:
        if not rest:
            return None
        return ResolvedModel(provider=None, upstream_id=rest, public_id=model_id)
    for provider in providers:
        if provider.prefix and first == provider.prefix:
            if rest and rest in await _get_cached_ids(provider):
                return ResolvedModel(
                    provider=provider, upstream_id=rest, public_id=model_id
                )
            return None
    for provider in providers:
        if model_id in await _get_cached_ids(provider):
            logger.warning(
                "Deprecated un-prefixed passthrough model id %r; use %s/%s",
                model_id,
                provider.prefix,
                model_id,
            )
            return ResolvedModel(
                provider=provider,
                upstream_id=model_id,
                public_id=f"{provider.prefix}/{model_id}",
            )
    return None


def _synthetic_entry(provider: Provider, model_id: str, with_details: bool) -> dict:
    """One peer-style entry so a passthrough model appears in /v1/models*
    alongside OpenTela-served models. Mirrors the shape produced by
    services.model_service.get_all_models — the frontend can't tell the
    difference. Empty peer_id/hostname/slurm/etc. drive ModelCard's
    passthrough branch to hide the irrelevant head rows."""
    entry = {
        "id": f"{provider.prefix}/{model_id}",
        "object": "model",
        "created": "0x",
        "owner": "0x",
        "peer_id": "",
        "hostname": "",
        "otela_version": "",
        "status": "ready",
        "labels": {
            "launched_by": provider.name,
            "framework": "vllm",
        },
        "worker_group_id": f"{provider.name}:{model_id}",
        "launched_by": provider.name,
        "slurm_job_id": "",
        "framework": "vllm",
        "started_at": "",
        "expires_at": "",
    }
    if with_details:
        entry["device"] = provider.device
    return entry


async def get_synthetic_entries(with_details: bool = False) -> list[dict]:
    """Synthesize peer-style entries across all configured passthrough
    providers. Returns an empty list when none are configured: we only
    advertise models we can actually serve. Provider prefixes make the
    listed ids structurally collision-free — the same upstream model on
    two providers appears as two distinct, individually-routable rows."""
    entries: list[dict] = []
    for provider in registered_providers():
        for model_id in sorted(await _get_cached_ids(provider)):
            entries.append(_synthetic_entry(provider, model_id, with_details))
    return entries
