"""Unit tests for the OpenAI-compatible passthrough registry — provider
prefix namespacing, dynamic model discovery with per-provider TTL cache,
stale fallback, gating on configuration, and un-prefixed back-compat."""

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
    resolve_model,
)

APERTUS_8B = "swiss-ai/Apertus-8B-Instruct-2509"
APERTUS_70B = "swiss-ai/Apertus-70B-Instruct-2509"


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


def test_resolve_none_when_unconfigured():
    """No provider configured → even a known prefixed id falls through to
    OpenTela (which 404s cleanly)."""
    with _patch_settings(_FakeSettings(cscs_l1_base_url="", cscs_l1_api_key="")):
        assert _run(resolve_model(f"CSCS-Inference/{APERTUS_8B}")) is None
        assert _run(resolve_model(APERTUS_8B)) is None


def test_synthetic_entries_empty_when_unconfigured():
    with _patch_settings(_FakeSettings(cscs_l1_base_url="", cscs_l1_api_key="")):
        assert _run(get_synthetic_entries()) == []


# ── prefix resolution ───────────────────────────────────────────────────────


def test_prefixed_id_resolves_to_provider_and_upstream_id():
    with _patch_settings(_FakeSettings()), _patch_fetch([APERTUS_8B, APERTUS_70B]):
        resolved = _run(resolve_model(f"CSCS-Inference/{APERTUS_8B}"))
    assert resolved is not None
    assert resolved.provider.name == "cscs_L1"
    assert resolved.upstream_id == APERTUS_8B
    assert resolved.public_id == f"CSCS-Inference/{APERTUS_8B}"


def test_prefixed_id_with_unknown_remainder_does_not_route():
    """A prefix claim for a model the provider doesn't advertise must not
    fall through to OpenTela under another name — None here means the full
    (never-launched) id 404s downstream."""
    with _patch_settings(_FakeSettings()), _patch_fetch([APERTUS_8B]):
        assert _run(resolve_model("CSCS-Inference/not-hosted")) is None
        assert _run(resolve_model("CSCS-Inference/")) is None
        assert _run(resolve_model("CSCS-Inference")) is None


def test_unknown_prefix_falls_through():
    with _patch_settings(_FakeSettings()), _patch_fetch([APERTUS_8B]):
        assert _run(resolve_model("Nonexistent-Provider/some/model")) is None
        assert _run(resolve_model("some/local-model")) is None
        assert _run(resolve_model("")) is None


def test_platform_prefix_resolves_to_opentela():
    """SwissAIResearch/... is this platform's own namespace: provider is
    None (caller routes to OpenTela), the forwarded id is bare, and the
    public id is preserved for response rewriting."""
    with _patch_settings(_FakeSettings()), _patch_fetch([APERTUS_8B]):
        resolved = _run(resolve_model(f"SwissAIResearch/{APERTUS_8B}"))
    assert resolved is not None
    assert resolved.provider is None
    assert resolved.upstream_id == APERTUS_8B
    assert resolved.public_id == f"SwissAIResearch/{APERTUS_8B}"


def test_platform_prefix_works_without_any_provider_configured():
    """The platform namespace is independent of passthrough config — it
    must resolve even when no external provider env is set."""
    with _patch_settings(_FakeSettings(cscs_l1_base_url="", cscs_l1_api_key="")):
        resolved = _run(resolve_model("SwissAIResearch/some/local-model"))
        assert resolved is not None and resolved.provider is None
        assert resolved.upstream_id == "some/local-model"
        assert _run(resolve_model("SwissAIResearch")) is None
        assert _run(resolve_model("SwissAIResearch/")) is None


def test_bare_upstream_id_still_routes_for_back_compat():
    """Deprecation window: clients using the historical un-prefixed ids
    keep working, and the resolution carries the prefixed public_id so
    responses advertise the migration target."""
    with _patch_settings(_FakeSettings()), _patch_fetch([APERTUS_8B]):
        resolved = _run(resolve_model(APERTUS_8B))
    assert resolved is not None
    assert resolved.provider.name == "cscs_L1"
    assert resolved.upstream_id == APERTUS_8B
    assert resolved.public_id == f"CSCS-Inference/{APERTUS_8B}"


# ── listing ─────────────────────────────────────────────────────────────────


def test_synthetic_entries_are_prefixed():
    with (
        _patch_settings(_FakeSettings()),
        _patch_fetch(["foo/new-model", APERTUS_8B]),
    ):
        entries = _run(get_synthetic_entries(with_details=True))
    ids = {e["id"] for e in entries}
    assert ids == {
        "CSCS-Inference/foo/new-model",
        f"CSCS-Inference/{APERTUS_8B}",
    }
    for e in entries:
        assert e["launched_by"] == "cscs_L1"
        assert e["framework"] == "vllm"
        assert e["device"] == "CSCS L1"
        # Empty fields drive ModelCard's passthrough branch to show only
        # model/launched_by/framework — keep them empty on the wire.
        assert e["slurm_job_id"] == ""
        assert e["started_at"] == ""
        assert e["expires_at"] == ""


def test_same_upstream_id_on_two_providers_lists_two_rows():
    """Prefixes make cross-provider collisions structurally impossible:
    the same upstream model on CSCS and RCP is two distinct rows, each
    individually routable. Bare-id back-compat picks the first provider
    in registration order."""
    settings = _FakeSettings(rcp_base_url="https://rcp/v1", rcp_api_key="rcp-key")
    with _patch_settings(settings), _patch_fetch([APERTUS_8B]):
        entries = _run(get_synthetic_entries())
        ids = {e["id"] for e in entries}
        via_cscs = _run(resolve_model(f"CSCS-Inference/{APERTUS_8B}"))
        via_rcp = _run(resolve_model(f"RCP-AIaaS/{APERTUS_8B}"))
        bare = _run(resolve_model(APERTUS_8B))
    assert ids == {f"CSCS-Inference/{APERTUS_8B}", f"RCP-AIaaS/{APERTUS_8B}"}
    assert via_cscs.provider.name == "cscs_L1"
    assert via_rcp.provider.name == "rcp"
    assert via_rcp.provider.api_key == "rcp-key"
    assert bare.provider.name == "cscs_L1"  # registered before rcp


# ── discovery cache ─────────────────────────────────────────────────────────


def test_fetch_cached_within_ttl():
    """Successive calls within the TTL hit cache, not re-fetch — stops us
    hammering the upstream on every page load + completion dispatch."""
    fake = AsyncMock(return_value={APERTUS_8B})
    with (
        _patch_settings(_FakeSettings()),
        patch.object(passthrough_service, "_fetch_model_ids", new=fake),
    ):
        _run(resolve_model(f"CSCS-Inference/{APERTUS_8B}"))
        _run(resolve_model(f"CSCS-Inference/{APERTUS_8B}"))
        _run(resolve_model("CSCS-Inference/anything"))
    assert fake.await_count == 1


# ── allowlist curation ──────────────────────────────────────────────────────


def test_rcp_allowlist_is_the_two_apertus_instruct_models():
    """Guard the curated set so a stray edit can't silently widen it."""
    assert _RCP_ALLOWED_MODEL_IDS == frozenset({APERTUS_8B, APERTUS_70B})


def test_cscs_l1_is_unrestricted():
    """CSCS L1 has no allowlist: everything it advertises is listed and
    routable, including quant variants and non-Apertus ids."""
    upstream = [APERTUS_8B, f"{APERTUS_8B}-FP8", "meta-llama/Llama-3-8B"]
    with _patch_settings(_FakeSettings()), _patch_fetch(upstream):
        listed = {e["id"] for e in _run(get_synthetic_entries())}
        for model_id in upstream:
            resolved = _run(resolve_model(f"CSCS-Inference/{model_id}"))
            assert resolved is not None and resolved.provider.name == "cscs_L1"
    assert listed == {f"CSCS-Inference/{m}" for m in upstream}


def test_off_allowlist_ids_are_filtered_from_rcp_listing_and_routing():
    """RCP advertises many models (incl. quant variants and a bare-prefix
    id) but surfaces ONLY the two allowlisted ids, and only those route —
    with or without the namespace prefix."""
    upstream = [
        APERTUS_8B,
        APERTUS_70B,
        f"{APERTUS_8B}-FP8",  # quant variant
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
        assert _run(resolve_model(f"RCP-AIaaS/{APERTUS_8B}")) is not None
        assert _run(resolve_model(f"RCP-AIaaS/{APERTUS_8B}-FP8")) is None
        assert _run(resolve_model("RCP-AIaaS/meta-llama/Llama-3-8B")) is None
        assert _run(resolve_model(f"{APERTUS_8B}-FP8")) is None
        assert _run(resolve_model("meta-llama/Llama-3-8B")) is None
    assert listed == {f"RCP-AIaaS/{APERTUS_8B}", f"RCP-AIaaS/{APERTUS_70B}"}


# ── failure modes ───────────────────────────────────────────────────────────


def test_cold_start_fetch_failure_falls_back_for_cscs_l1():
    """If CSCS L1 is unreachable on the very first call, surface its
    fallback list so the Apertus rows still appear instead of vanishing."""
    with _patch_settings(_FakeSettings()), _patch_fetch(None):
        entries = _run(get_synthetic_entries())
    assert {e["id"] for e in entries} == {
        f"CSCS-Inference/{m}" for m in _CSCS_L1_FALLBACK_IDS
    }


def test_cold_start_failure_no_fallback_for_provider_without_one():
    """A provider with no fallback_ids (e.g. RCP) advertises nothing until
    its first successful fetch — not a stale Apertus list."""
    rcp = Provider(
        name="rcp",
        base_url="https://rcp/v1",
        api_key="k",
        device="EPFL RCP",
        prefix="RCP-AIaaS",
    )
    with _patch_fetch(None):
        assert _run(passthrough_service._get_cached_ids(rcp)) == set()


def test_stale_cache_preferred_over_fallback_after_initial_success():
    """Once we've fetched successfully, a later fetch failure keeps serving
    the real (stale) set rather than resetting to the fallback."""
    fake = AsyncMock(side_effect=[{APERTUS_8B}, None])
    with (
        _patch_settings(_FakeSettings()),
        patch.object(passthrough_service, "_fetch_model_ids", new=fake),
    ):
        first = _run(get_synthetic_entries())
        # Expire the cache and call again; second fetch fails.
        passthrough_service._cache["cscs_L1"]["fetched_at"] = 0.0
        second = _run(get_synthetic_entries())
    assert {e["id"] for e in first} == {f"CSCS-Inference/{APERTUS_8B}"}
    assert {e["id"] for e in second} == {f"CSCS-Inference/{APERTUS_8B}"}


# ── helpers ─────────────────────────────────────────────────────────────────


def test_endpoint_strips_trailing_slash():
    """Callers append /chat/completions etc., so a trailing slash would
    produce a double-slash URL — strip it defensively."""
    p = Provider(name="x", base_url="https://l1/v1/", api_key="k", device="X")
    assert endpoint(p) == "https://l1/v1"
