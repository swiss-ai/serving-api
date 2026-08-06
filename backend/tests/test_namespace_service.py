"""Unit tests for served-name namespacing: the ``<username>/<vendor>/<model>``
grammar, the TTL-cached DNT-derived model → launched_by map, and the
ensure_namespace_ok decision matrix incl. its fail-open policy."""

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from backend.services import namespace_service
from backend.services.namespace_service import (
    _build_launcher_map,
    _reset_cache_for_tests,
    ensure_namespace_ok,
    namespace_matches,
    namespace_of,
)


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _clear_cache():
    """Each test starts with a cold launcher-map cache so state doesn't leak."""
    _reset_cache_for_tests()
    yield
    _reset_cache_for_tests()


def _patch_fetch_dnt(data_or_none):
    return patch.object(
        namespace_service, "_fetch_dnt", new=AsyncMock(return_value=data_or_none)
    )


def _patch_no_passthrough():
    return patch.object(
        namespace_service, "resolve_provider", new=AsyncMock(return_value=None)
    )


def _dnt_peer(model_id: str, launched_by: str | None = None) -> dict:
    labels = {"worker_group_id": "wg"}
    if launched_by is not None:
        labels["launched_by"] = launched_by
    return {
        "id": "QmPeer",
        "labels": labels,
        "service": [
            {"name": "llm", "identity_group": [f"model={model_id}"]},
        ],
    }


# ── name grammar ────────────────────────────────────────────────────────────


def test_namespace_of_only_reads_three_segment_names():
    assert namespace_of("alice/swiss-ai/Apertus-8B") == "alice"
    assert namespace_of("swiss-ai/Apertus-8B") is None
    assert namespace_of("Apertus-8B") is None


def test_namespace_matches_is_lenient_where_there_is_nothing_to_check():
    # Pre-namespacing ids carry no username...
    assert namespace_matches("swiss-ai/Apertus-8B", "bob") is True
    # ...and a peer from an OpenTela build that emits no labels carries no
    # launching account to compare against.
    assert namespace_matches("alice/swiss-ai/Apertus-8B", "") is True


def test_namespace_matches_compares_case_insensitively():
    assert namespace_matches("alice/swiss-ai/Apertus-8B", "Alice") is True
    assert namespace_matches("alice/swiss-ai/Apertus-8B", " alice ") is True
    assert namespace_matches("alice/swiss-ai/Apertus-8B", "bob") is False


# ── the DNT-derived launcher map ────────────────────────────────────────────


def test_launcher_map_collects_every_peer_serving_a_name():
    data = {
        "/legit": _dnt_peer("alice/swiss-ai/Apertus-8B", launched_by="alice"),
        "/squat": _dnt_peer("alice/swiss-ai/Apertus-8B", launched_by="bob"),
    }
    assert _build_launcher_map(data) == {"alice/swiss-ai/Apertus-8B": ["alice", "bob"]}


def test_launcher_map_falls_back_to_served_model_name_for_pending_peers():
    """A peer with no advertised service yet (booting, or a metrics-only
    follower) still declares the name it will serve — mirroring how
    model_service surfaces it in the model list."""
    data = {
        "/pending": {
            "id": "QmPeer",
            "labels": {
                "worker_group_id": "wg",
                "launched_by": "bob",
                "served_model_name": "alice/swiss-ai/Apertus-8B",
            },
            "service": [],
        }
    }
    assert _build_launcher_map(data) == {"alice/swiss-ai/Apertus-8B": ["bob"]}


# ── routing decisions ───────────────────────────────────────────────────────


def test_namespaced_model_routes_for_its_own_launcher():
    with (
        _patch_no_passthrough(),
        _patch_fetch_dnt(
            {"/p": _dnt_peer("alice/swiss-ai/Apertus-8B", launched_by="alice")}
        ),
    ):
        _run(ensure_namespace_ok("alice/swiss-ai/Apertus-8B"))


def test_squatted_namespace_is_refused_for_everyone():
    """A peer publishing under someone else's username poisons the id:
    OpenTela balances the name across every peer advertising it, so we
    can't keep a request off the squatter — refuse the whole id."""
    with (
        _patch_no_passthrough(),
        _patch_fetch_dnt(
            {
                "/legit": _dnt_peer("alice/swiss-ai/Apertus-8B", launched_by="alice"),
                "/squat": _dnt_peer("alice/swiss-ai/Apertus-8B", launched_by="bob"),
            }
        ),
    ):
        with pytest.raises(HTTPException) as exc_info:
            _run(ensure_namespace_ok("alice/swiss-ai/Apertus-8B"))
    assert exc_info.value.status_code == 403
    assert "namespace" in exc_info.value.detail


def test_unnamespaced_legacy_model_is_unaffected():
    """Launches that predate namespacing keep working — a 2-segment id has
    no username in it, whatever the peer's launched_by says."""
    with (
        _patch_no_passthrough(),
        _patch_fetch_dnt({"/p": _dnt_peer("swiss-ai/Apertus-8B", launched_by="bob")}),
    ):
        _run(ensure_namespace_ok("swiss-ai/Apertus-8B"))


def test_peer_without_launched_by_label_is_unaffected():
    """OpenTela <v0.0.6 emits no labels — there is nothing to check the
    namespace against, and refusing would break every such launch."""
    with (
        _patch_no_passthrough(),
        _patch_fetch_dnt({"/p": _dnt_peer("alice/swiss-ai/Apertus-8B")}),
    ):
        _run(ensure_namespace_ok("alice/swiss-ai/Apertus-8B"))


def test_unknown_model_falls_through_to_upstream():
    """Not in the DNT: leave it alone so the proxy 404s as it always has,
    rather than inventing a 403 for a model that doesn't exist."""
    with (
        _patch_no_passthrough(),
        _patch_fetch_dnt({"/p": _dnt_peer("alice/swiss-ai/Apertus-8B", "alice")}),
    ):
        _run(ensure_namespace_ok("nobody/who/knows"))


def test_passthrough_provider_ids_skip_the_check():
    """Passthrough ids are never namespaced and never on the mesh."""
    with (
        patch.object(
            namespace_service, "resolve_provider", new=AsyncMock(return_value=object())
        ),
        _patch_fetch_dnt(None) as fake,
    ):
        _run(ensure_namespace_ok("swiss-ai/Apertus-8B-Instruct-2509"))
    assert fake.await_count == 0


def test_non_string_model_is_ignored():
    """A client sending {"model": 123} must not 500 on str.split — let the
    request fall through to upstream validation."""
    with _patch_no_passthrough():
        _run(ensure_namespace_ok(123))
        _run(ensure_namespace_ok(None))
        _run(ensure_namespace_ok(""))


# ── cache behaviour ─────────────────────────────────────────────────────────


def test_cold_start_fetch_failure_fails_open():
    """Never fetched successfully → allow and log, rather than taking the
    whole gateway down with the DNT."""
    with _patch_no_passthrough(), _patch_fetch_dnt(None):
        _run(ensure_namespace_ok("alice/swiss-ai/Apertus-8B"))


def test_stale_map_is_kept_when_a_refresh_fails():
    """A DNT blip must not silently un-enforce a squat that the last good
    fetch established."""
    squatted = {
        "/legit": _dnt_peer("alice/swiss-ai/Apertus-8B", launched_by="alice"),
        "/squat": _dnt_peer("alice/swiss-ai/Apertus-8B", launched_by="bob"),
    }
    with _patch_no_passthrough():
        with _patch_fetch_dnt(squatted):
            with pytest.raises(HTTPException):
                _run(ensure_namespace_ok("alice/swiss-ai/Apertus-8B"))
        # Expire the TTL, then fail the refresh.
        namespace_service._cache["fetched_at"] = 0.0
        with _patch_fetch_dnt(None):
            with pytest.raises(HTTPException):
                _run(ensure_namespace_ok("alice/swiss-ai/Apertus-8B"))


def test_within_ttl_only_one_fetch_happens():
    """Burst traffic for one model coalesces to a single DNT fetch."""
    with (
        _patch_no_passthrough(),
        _patch_fetch_dnt({"/p": _dnt_peer("swiss-ai/Apertus-8B", "bob")}) as fake,
    ):
        for _ in range(5):
            _run(ensure_namespace_ok("swiss-ai/Apertus-8B"))
    assert fake.await_count == 1
    assert time.time() - namespace_service._cache["fetched_at"] < 10


# ── fixture-mode DNT source ─────────────────────────────────────────────────


def test_fetch_dnt_reads_fixture_file(tmp_path):
    """OTELA_FIXTURE_PATH set → the launcher map reads the same on-disk JSON
    the models router serves, not HTTP."""
    import json

    fixture = tmp_path / "dnt.json"
    fixture.write_text(
        json.dumps({"/p": _dnt_peer("alice/swiss-ai/Apertus-8B", launched_by="alice")})
    )
    with patch.object(namespace_service, "_dnt_endpoint", return_value=str(fixture)):
        data = _run(namespace_service._fetch_dnt())
    assert _build_launcher_map(data) == {"alice/swiss-ai/Apertus-8B": ["alice"]}
