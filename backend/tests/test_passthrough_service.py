"""Unit tests for the OpenAI-compatible passthrough registry — dynamic
model discovery with per-provider TTL cache, stale fallback, gating on
configuration, and multi-provider routing/precedence."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from backend.services import passthrough_service
from backend.services.passthrough_service import (
    Provider,
    _CSCS_L1_FALLBACK_IDS,
    _RCP_ALLOWED_MODEL_IDS,
    _reset_cache_for_tests,
    endpoint,
    get_synthetic_entries,
    registered_providers,
    resolve_provider,
)


class _FakeSettings:
    """Stand-in for Settings carrying the passthrough provider env pairs.
    Defaults to CSCS L1 configured + RCP unconfigured; override per test."""

    def __init__(
        self,
        cscs_l1_base_url="https://l1/v1",
        cscs_l1_api_key="k",
        rcp_base_url="",
        rcp_api_key="",
    ):
        self.cscs_l1_base_url = cscs_l1_base_url
        self.cscs_l1_api_key = cscs_l1_api_key
        self.rcp_base_url = rcp_base_url
        self.rcp_api_key = rcp_api_key


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _clear_cache():
    """Each test starts with a cold cache so cache state doesn't leak."""
    _reset_cache_for_tests()
    yield
    _reset_cache_for_tests()


def _patch_settings(settings):
    return patch.object(passthrough_service, "get_settings", return_value=settings)


def _patch_fetch(ids_or_none):
    """Patch _fetch_model_ids with an AsyncMock. Pass a list → returns a
    set; pass None → simulates fetch failure for every provider."""
    value = set(ids_or_none) if ids_or_none is not None else None
    return patch.object(
        passthrough_service, "_fetch_model_ids", new=AsyncMock(return_value=value)
    )


# ── configuration gating ────────────────────────────────────────────────────


def test_no_providers_when_unconfigured():
    with _patch_settings(_FakeSettings(cscs_l1_base_url="", cscs_l1_api_key="")):
        assert registered_providers() == []


def test_half_configured_provider_skipped():
    """Both env vars required — URL without key should not register."""
    with _patch_settings(
        _FakeSettings(cscs_l1_base_url="https://l1/v1", cscs_l1_api_key="")
    ):
        assert registered_providers() == []


def test_resolve_false_when_unconfigured():
    """No provider configured → even a known id falls through to OpenTela."""
    with _patch_settings(_FakeSettings(cscs_l1_base_url="", cscs_l1_api_key="")):
        assert _run(resolve_provider("swiss-ai/Apertus-8B-Instruct-2509")) is None


def test_synthetic_entries_empty_when_unconfigured():
    with _patch_settings(_FakeSettings(cscs_l1_base_url="", cscs_l1_api_key="")):
        assert _run(get_synthetic_entries()) == []


# ── happy path: fetch + cache ───────────────────────────────────────────────


def test_resolve_routes_to_fetched_ids():
    """Membership reflects whatever the upstream currently exposes."""
    with (
        _patch_settings(_FakeSettings()),
        _patch_fetch(
            [
                "swiss-ai/Apertus-8B-Instruct-2509",
                "swiss-ai/Apertus-70B-Instruct-2509",
            ]
        ),
    ):
        p = _run(resolve_provider("swiss-ai/Apertus-8B-Instruct-2509"))
        assert p is not None and p.name == "cscs_L1"
        assert (
            _run(resolve_provider("swiss-ai/Apertus-70B-Instruct-2509")).name
            == "cscs_L1"
        )
        assert _run(resolve_provider("not-hosted")) is None


def test_synthetic_entries_built_from_fetched_ids():
    with (
        _patch_settings(_FakeSettings()),
        # CSCS L1 is unrestricted (allowed_ids=None): everything it
        # advertises surfaces, including ids we don't serve ourselves.
        _patch_fetch(["foo/new-model", "swiss-ai/Apertus-8B-Instruct-2509"]),
    ):
        entries = _run(get_synthetic_entries(with_details=True))
    ids = {e["id"] for e in entries}
    assert ids == {"foo/new-model", "swiss-ai/Apertus-8B-Instruct-2509"}
    for e in entries:
        assert e["launched_by"] == "cscs_L1"
        assert e["framework"] == "vllm"
        assert e["device"] == "CSCS L1"
        # Empty fields drive ModelCard's passthrough branch to show only
        # model/launched_by/framework — keep them empty on the wire.
        assert e["slurm_job_id"] == ""
        assert e["started_at"] == ""
        assert e["expires_at"] == ""


def test_fetch_cached_within_ttl():
    """Successive calls within the TTL hit cache, not re-fetch — stops us
    hammering the upstream on every page load + completion dispatch."""
    fake = AsyncMock(return_value={"swiss-ai/Apertus-8B-Instruct-2509"})
    with (
        _patch_settings(_FakeSettings()),
        patch.object(passthrough_service, "_fetch_model_ids", new=fake),
    ):
        _run(resolve_provider("swiss-ai/Apertus-8B-Instruct-2509"))
        _run(resolve_provider("swiss-ai/Apertus-8B-Instruct-2509"))
        _run(resolve_provider("anything"))
    assert fake.await_count == 1


# ── allowlist curation ──────────────────────────────────────────────────────


def test_rcp_allowlist_is_the_two_apertus_instruct_models():
    """Guard the curated set so a stray edit can't silently widen it."""
    assert _RCP_ALLOWED_MODEL_IDS == frozenset(
        {
            "swiss-ai/Apertus-8B-Instruct-2509",
            "swiss-ai/Apertus-70B-Instruct-2509",
        }
    )


def test_cscs_l1_is_unrestricted():
    """CSCS L1 has no allowlist: everything it advertises is listed and
    routable, including quant variants and non-Apertus ids."""
    upstream = [
        "swiss-ai/Apertus-8B-Instruct-2509",
        "swiss-ai/Apertus-8B-Instruct-2509-FP8",
        "meta-llama/Llama-3-8B",
    ]
    with _patch_settings(_FakeSettings()), _patch_fetch(upstream):
        listed = {e["id"] for e in _run(get_synthetic_entries())}
        for model_id in upstream:
            assert _run(resolve_provider(model_id)).name == "cscs_L1"
    assert listed == set(upstream)


def test_off_allowlist_ids_are_filtered_from_rcp_listing_and_routing():
    """RCP advertises many models (incl. quant variants and a bare-prefix
    id) but surfaces ONLY the two allowlisted ids, and only those route."""
    upstream = [
        "swiss-ai/Apertus-8B-Instruct-2509",
        "swiss-ai/Apertus-70B-Instruct-2509",
        "swiss-ai/Apertus-8B-Instruct-2509-FP8",  # quant variant
        "Apertus-8B-Instruct-2509",  # bare, no org prefix
        "meta-llama/Llama-3-8B",
    ]
    settings = _FakeSettings(
        cscs_l1_base_url="",
        cscs_l1_api_key="",
        rcp_base_url="https://rcp/v1",
        rcp_api_key="rcp-key",
    )
    with _patch_settings(settings), _patch_fetch(upstream):
        listed = {e["id"] for e in _run(get_synthetic_entries())}
        assert _run(resolve_provider("swiss-ai/Apertus-8B-Instruct-2509")) is not None
        assert _run(resolve_provider("swiss-ai/Apertus-8B-Instruct-2509-FP8")) is None
        assert _run(resolve_provider("Apertus-8B-Instruct-2509")) is None
        assert _run(resolve_provider("meta-llama/Llama-3-8B")) is None
    assert listed == {
        "swiss-ai/Apertus-8B-Instruct-2509",
        "swiss-ai/Apertus-70B-Instruct-2509",
    }


# ── failure modes ───────────────────────────────────────────────────────────


def test_cold_start_fetch_failure_falls_back_for_cscs_l1():
    """If CSCS L1 is unreachable on the very first call, surface its
    fallback list so the Apertus rows still appear instead of vanishing."""
    with _patch_settings(_FakeSettings()), _patch_fetch(None):
        entries = _run(get_synthetic_entries())
    assert {e["id"] for e in entries} == set(_CSCS_L1_FALLBACK_IDS)


def test_cold_start_failure_no_fallback_for_provider_without_one():
    """A provider with no fallback_ids (e.g. RCP) advertises nothing until
    its first successful fetch — not a stale Apertus list."""
    rcp = Provider(
        name="rcp", base_url="https://rcp/v1", api_key="k", device="EPFL RCP"
    )
    with _patch_fetch(None):
        assert _run(passthrough_service._get_cached_ids(rcp)) == set()


def test_stale_cache_preferred_over_fallback_after_initial_success():
    """Once we've fetched successfully, a later fetch failure keeps serving
    the real (stale) set rather than resetting to the fallback."""
    allowed = "swiss-ai/Apertus-8B-Instruct-2509"
    fake = AsyncMock(side_effect=[{allowed}, None])
    with (
        _patch_settings(_FakeSettings()),
        patch.object(passthrough_service, "_fetch_model_ids", new=fake),
    ):
        first = _run(get_synthetic_entries())
        # Expire the cache and call again; second fetch fails.
        passthrough_service._cache["cscs_L1"]["fetched_at"] = 0.0
        second = _run(get_synthetic_entries())
    assert {e["id"] for e in first} == {allowed}
    assert {e["id"] for e in second} == {allowed}


# ── multi-provider routing + precedence ─────────────────────────────────────


def test_rcp_provider_routes_independently():
    """An RCP-only model id resolves to the RCP provider with its own
    endpoint + key, while OpenTela models still fall through."""
    settings = _FakeSettings(
        cscs_l1_base_url="",
        cscs_l1_api_key="",
        rcp_base_url="https://rcp/v1",
        rcp_api_key="rcp-key",
    )
    with (
        _patch_settings(settings),
        _patch_fetch(["swiss-ai/Apertus-70B-Instruct-2509"]),
    ):
        p = _run(resolve_provider("swiss-ai/Apertus-70B-Instruct-2509"))
    assert p is not None and p.name == "rcp"
    assert endpoint(p) == "https://rcp/v1"
    assert p.api_key == "rcp-key"


def test_collision_first_registered_provider_wins():
    """When two providers expose the same id, the earlier one in
    registration order (CSCS L1) wins routing and the duplicate is not
    double-listed."""
    settings = _FakeSettings(rcp_base_url="https://rcp/v1", rcp_api_key="rcp-key")
    shared_id = "swiss-ai/Apertus-8B-Instruct-2509"
    # Both providers return the same id from /models.
    with _patch_settings(settings), _patch_fetch([shared_id]):
        p = _run(resolve_provider(shared_id))
        entries = _run(get_synthetic_entries())
    assert p.name == "cscs_L1"  # registered before rcp
    shared = [e for e in entries if e["id"] == shared_id]
    assert len(shared) == 1
    assert shared[0]["launched_by"] == "cscs_L1"


# ── helpers ─────────────────────────────────────────────────────────────────


def test_endpoint_strips_trailing_slash():
    """Callers append /chat/completions etc., so a trailing slash would
    produce a double-slash URL — strip it defensively."""
    p = Provider(name="x", base_url="https://l1/v1/", api_key="k", device="X")
    assert endpoint(p) == "https://l1/v1"
