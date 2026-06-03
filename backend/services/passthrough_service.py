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

Model ids are matched verbatim (no normalization yet). When two
providers expose the same id, the first one in `registered_providers()`
wins routing and listing — registration order is precedence.

Secrets (base URLs, API keys) come from env via Settings.
"""

import asyncio
import time
from dataclasses import dataclass

import aiohttp

from backend.config import get_settings


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
    # Cold-start backstop, used only if we haven't successfully fetched
    # /models yet AND the current fetch fails. Empty = nothing advertised
    # until the first successful fetch.
    fallback_ids: tuple[str, ...] = ()


# CSCS L1 cold-start fallback so the Apertus rows don't vanish during a
# brief L1 outage on the very first fetch. New providers (e.g. RCP) get
# no fallback — we just wait for the first successful discovery.
_CSCS_L1_FALLBACK_IDS: tuple[str, ...] = (
    "Apertus-70B-Instruct-2509",
    "Apertus-8B-Instruct-2509",
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
    """Return the provider's model id set. Refreshes if TTL has expired;
    on fetch failure keeps stale cache, falling back to
    ``provider.fallback_ids`` only at true cold start. A transient
    upstream outage shouldn't make rows that *were* there disappear."""
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


async def resolve_provider(model_id: str) -> Provider | None:
    """Return the configured passthrough Provider that serves ``model_id``,
    or None so the caller falls through to OpenTela (which 404s cleanly).
    First match in registration order wins on id collisions. With no
    provider configured this is always None, so passthrough model ids fall
    through instead of producing an opaque connection error."""
    if not model_id:
        return None
    for provider in registered_providers():
        if model_id in await _get_cached_ids(provider):
            return provider
    return None


def _synthetic_entry(provider: Provider, model_id: str, with_details: bool) -> dict:
    """One peer-style entry so a passthrough model appears in /v1/models*
    alongside OpenTela-served models. Mirrors the shape produced by
    services.model_service.get_all_models — the frontend can't tell the
    difference. Empty peer_id/hostname/slurm/etc. drive ModelCard's
    passthrough branch to hide the irrelevant head rows."""
    entry = {
        "id": model_id,
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
    advertise models we can actually serve. On id collisions the earlier
    provider (registration order) wins and the duplicate is dropped, so a
    model is never double-listed."""
    entries: list[dict] = []
    seen: set[str] = set()
    for provider in registered_providers():
        for model_id in sorted(await _get_cached_ids(provider)):
            if model_id in seen:
                continue
            seen.add(model_id)
            entries.append(_synthetic_entry(provider, model_id, with_details))
    return entries
