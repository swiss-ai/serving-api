"""Unit tests for the CSCS L1 passthrough — dynamic model discovery
with TTL cache, stale fallback, and gating on configuration."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from backend.services import cscs_l1_service
from backend.services.cscs_l1_service import (
    FALLBACK_MODEL_IDS,
    _reset_cache_for_tests,
    get_l1_synthetic_entries,
    is_l1_model,
    l1_api_key,
    l1_endpoint,
)


class _FakeSettings:
    def __init__(self, base_url="", api_key=""):
        self.cscs_l1_base_url = base_url
        self.cscs_l1_api_key = api_key


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _clear_cache():
    """Each test starts with a cold cache so cache state doesn't leak
    across cases."""
    _reset_cache_for_tests()
    yield
    _reset_cache_for_tests()


# ── configuration gating ────────────────────────────────────────────────────


def test_is_l1_model_false_when_unconfigured():
    """If L1 env isn't set, even a known L1 model id returns False so
    completion routing falls through to OpenTela for a clean 404."""
    with patch.object(
        cscs_l1_service, "get_settings", return_value=_FakeSettings("", "")
    ):
        assert _run(is_l1_model("Apertus-8B-Instruct-2509")) is False


def test_is_l1_model_false_when_half_configured():
    """Both env vars required — partial config (URL but no key) should
    not trigger L1 routing."""
    with patch.object(
        cscs_l1_service, "get_settings", return_value=_FakeSettings("https://l1/v1", "")
    ):
        assert _run(is_l1_model("Apertus-8B-Instruct-2509")) is False


def test_synthetic_entries_empty_when_unconfigured():
    """Don't advertise L1 models if we can't actually proxy them."""
    with patch.object(
        cscs_l1_service, "get_settings", return_value=_FakeSettings("", "")
    ):
        assert _run(get_l1_synthetic_entries()) == []


# ── happy path: fetch + cache ───────────────────────────────────────────────


def _patch_fetch(ids_or_none):
    """Patch _fetch_l1_model_ids with an AsyncMock returning the given
    value. Pass a list to return a set; pass None to simulate fetch
    failure."""
    value = set(ids_or_none) if ids_or_none is not None else None
    return patch.object(
        cscs_l1_service, "_fetch_l1_model_ids", new=AsyncMock(return_value=value)
    )


def test_is_l1_model_routes_to_fetched_ids():
    """Membership reflects whatever L1 currently exposes — not a hardcoded list."""
    with (
        patch.object(
            cscs_l1_service,
            "get_settings",
            return_value=_FakeSettings("https://l1/v1", "k"),
        ),
        _patch_fetch(["Apertus-8B-Instruct-2509", "Apertus-70B-Instruct-2509"]),
    ):
        assert _run(is_l1_model("Apertus-8B-Instruct-2509")) is True
        assert _run(is_l1_model("Apertus-70B-Instruct-2509")) is True
        assert _run(is_l1_model("not-on-l1")) is False


def test_synthetic_entries_built_from_fetched_ids():
    """The /v1/models entries surfaced to the frontend are built from
    whatever L1 reports — so a new model on L1 shows up without us
    deploying."""
    with (
        patch.object(
            cscs_l1_service,
            "get_settings",
            return_value=_FakeSettings("https://l1/v1", "k"),
        ),
        _patch_fetch(["foo/new-model", "Apertus-8B-Instruct-2509"]),
    ):
        entries = _run(get_l1_synthetic_entries(with_details=True))
    ids = {e["id"] for e in entries}
    assert ids == {"foo/new-model", "Apertus-8B-Instruct-2509"}
    for e in entries:
        assert e["launched_by"] == "cscs_L1"
        assert e["framework"] == "vllm"
        assert e["device"] == "CSCS L1"
        # The empty fields are how the ModelCard's L1 branch ends up showing
        # only model/launched_by/framework — keep them empty on the wire.
        assert e["slurm_job_id"] == ""
        assert e["started_at"] == ""
        assert e["expires_at"] == ""


def test_fetch_cached_within_ttl():
    """Successive calls within the TTL should hit cache, not re-fetch.
    Stops us from hammering L1 on every /v1/models page load + every
    completion dispatch."""
    fake = AsyncMock(return_value={"Apertus-8B-Instruct-2509"})
    with (
        patch.object(
            cscs_l1_service,
            "get_settings",
            return_value=_FakeSettings("https://l1/v1", "k"),
        ),
        patch.object(cscs_l1_service, "_fetch_l1_model_ids", new=fake),
    ):
        _run(is_l1_model("Apertus-8B-Instruct-2509"))
        _run(is_l1_model("Apertus-8B-Instruct-2509"))
        _run(is_l1_model("anything"))
    assert fake.await_count == 1


# ── failure modes ───────────────────────────────────────────────────────────


def test_cold_start_fetch_failure_falls_back_to_hardcoded_list():
    """If L1 is unreachable on the very first call, surface the
    hardcoded fallback so the Apertus rows still appear in the model
    list instead of mysteriously vanishing."""
    with (
        patch.object(
            cscs_l1_service,
            "get_settings",
            return_value=_FakeSettings("https://l1/v1", "k"),
        ),
        _patch_fetch(None),
    ):
        entries = _run(get_l1_synthetic_entries())
    ids = {e["id"] for e in entries}
    assert ids == set(FALLBACK_MODEL_IDS)


def test_stale_cache_preferred_over_fallback_after_initial_success():
    """Once we've fetched successfully, a subsequent fetch failure
    should keep serving the *real* set (stale cache) rather than reset
    to the fallback. The fallback only exists to backstop cold start —
    we don't want a transient outage to drop models that *were* there."""
    fake = AsyncMock(side_effect=[{"custom/only-on-l1"}, None])

    with (
        patch.object(
            cscs_l1_service,
            "get_settings",
            return_value=_FakeSettings("https://l1/v1", "k"),
        ),
        patch.object(cscs_l1_service, "_fetch_l1_model_ids", new=fake),
    ):
        first = _run(get_l1_synthetic_entries())
        # Expire the cache and call again; second fetch fails.
        cscs_l1_service._cache["fetched_at"] = 0.0
        second = _run(get_l1_synthetic_entries())
    assert {e["id"] for e in first} == {"custom/only-on-l1"}
    # Stale cache (real set) preserved, NOT fallback.
    assert {e["id"] for e in second} == {"custom/only-on-l1"}


# ── helpers ─────────────────────────────────────────────────────────────────


def test_l1_endpoint_strips_trailing_slash():
    """Callers append /chat/completions etc., so a trailing slash here
    would produce a double-slash URL — strip it defensively."""
    with patch.object(
        cscs_l1_service,
        "get_settings",
        return_value=_FakeSettings("https://l1/v1/", "k"),
    ):
        assert l1_endpoint() == "https://l1/v1"


def test_l1_api_key_reads_settings():
    with patch.object(
        cscs_l1_service,
        "get_settings",
        return_value=_FakeSettings("https://l1/v1", "sk-secret"),
    ):
        assert l1_api_key() == "sk-secret"
