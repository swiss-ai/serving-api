"""CSCS L1 passthrough.

CSCS already serves a small set of OpenAI-compatible models on their L1
endpoint. Instead of launching duplicate pods for them ourselves, we
forward those model ids to L1 and surface them in /v1/models alongside
our locally-served models.

Discovery: we hit L1's own /models endpoint on first use (and every
30 s thereafter) so the set of L1-routable models tracks whatever L1
exposes, without code changes. A small `FALLBACK_MODEL_IDS` list
backstops the cold-start case when L1 is unreachable on the very first
fetch, so the model list isn't completely missing the Apertus rows
during a brief L1 outage.

Secrets (base URL, API key) come from env via Settings.
"""

import asyncio
import time

import aiohttp

from backend.config import get_settings


# Cold-start fallback. Used only if we haven't successfully fetched
# /models from L1 yet AND the current fetch fails. Once we've fetched
# once successfully, we keep serving the stale cache rather than fall
# back, so a transient outage never drops a model that *was* there.
FALLBACK_MODEL_IDS: list[str] = [
    "Apertus-70B-Instruct-2509",
    "Apertus-8B-Instruct-2509",
]

# 30 s strikes a balance: short enough that an L1 deployment of a new
# model is visible within half a minute, long enough that page reloads
# + completion dispatches don't hammer L1.
_CACHE_TTL_SECONDS = 30.0
# Timeout for the GET /models probe — keep tight so a wedged L1 can't
# stall /v1/models page loads on our side.
_FETCH_TIMEOUT_SECONDS = 5.0

_cache_lock = asyncio.Lock()
_cache: dict = {"fetched_at": 0.0, "ids": None}


def _l1_configured() -> bool:
    s = get_settings()
    return bool(s.cscs_l1_base_url and s.cscs_l1_api_key)


def l1_endpoint() -> str:
    """Base URL for L1 OpenAI-compatible API (e.g. https://.../v1).
    Caller appends /chat/completions etc."""
    return get_settings().cscs_l1_base_url.rstrip("/")


def l1_api_key() -> str:
    return get_settings().cscs_l1_api_key


def _reset_cache_for_tests() -> None:
    """Test helper — clears the cache so tests can simulate cold start
    without leaking state across cases."""
    _cache["fetched_at"] = 0.0
    _cache["ids"] = None


async def _fetch_l1_model_ids() -> set[str] | None:
    """GET {base}/models from L1. Returns None on any failure (network,
    non-200, malformed JSON) so the caller can decide whether to keep
    stale cache or fall back."""
    url = l1_endpoint() + "/models"
    headers = {"Authorization": f"Bearer {l1_api_key()}"}
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


async def _get_cached_ids() -> set[str]:
    """Return the L1 model id set. Refreshes if TTL has expired; on
    fetch failure keeps stale cache, falling back to FALLBACK_MODEL_IDS
    only at true cold start. Never returns an empty set when L1 is
    configured — a transient L1 outage shouldn't make the Apertus rows
    disappear from the model list."""
    if not _l1_configured():
        return set()

    now = time.time()
    if _cache["ids"] is not None and (now - _cache["fetched_at"]) < _CACHE_TTL_SECONDS:
        return _cache["ids"]

    async with _cache_lock:
        # Another coroutine may have refreshed while we waited on the lock.
        if (
            _cache["ids"] is not None
            and (time.time() - _cache["fetched_at"]) < _CACHE_TTL_SECONDS
        ):
            return _cache["ids"]

        fetched = await _fetch_l1_model_ids()
        if fetched is not None:
            _cache["ids"] = fetched
            _cache["fetched_at"] = time.time()
            return fetched

        if _cache["ids"] is not None:
            # Keep serving stale cache; don't update fetched_at so we
            # try again on the next call instead of waiting a full TTL.
            return _cache["ids"]

        return set(FALLBACK_MODEL_IDS)


async def is_l1_model(model_id: str) -> bool:
    """True only when the model is exposed by L1 AND L1 is configured —
    so an unconfigured deploy doesn't try to proxy to an empty URL. With
    L1 unconfigured, L1 model ids fall through to OpenTela (which 404s
    cleanly) instead of producing an opaque connection error."""
    if not model_id or not _l1_configured():
        return False
    ids = await _get_cached_ids()
    return model_id in ids


async def get_l1_synthetic_entries(with_details: bool = False) -> list[dict]:
    """Synthesize one peer-style entry per L1 model so they appear in
    /v1/models* alongside OpenTela-served models. Mirrors the shape
    produced by services.model_service.get_all_models — the frontend
    can't tell the difference.

    Returns an empty list when L1 isn't configured: we only advertise
    these models if we can actually serve them.
    """
    if not _l1_configured():
        return []

    ids = await _get_cached_ids()
    entries: list[dict] = []
    for model_id in sorted(ids):
        wg = f"cscs-l1:{model_id}"
        entry = {
            "id": model_id,
            "object": "model",
            "created": "0x",
            "owner": "0x",
            # Empty peer_id/hostname → ModelCard's L1 branch hides the
            # head row anyway; keep them blank rather than synthesise
            # fake values.
            "peer_id": "",
            "hostname": "",
            "otela_version": "",
            "status": "ready",
            "labels": {
                "launched_by": "cscs_L1",
                "framework": "vllm",
            },
            "worker_group_id": wg,
            "launched_by": "cscs_L1",
            "slurm_job_id": "",
            "framework": "vllm",
            "started_at": "",
            "expires_at": "",
        }
        if with_details:
            entry["device"] = "CSCS L1"
        entries.append(entry)
    return entries
