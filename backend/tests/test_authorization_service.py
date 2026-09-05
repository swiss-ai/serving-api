"""Unit tests for per-user model authorization: the ``authorization`` label
grammar, the Redis-cached DNT-derived model → authorization map, and the
ensure_model_access decision matrix incl. its fail-open policy."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from backend.redis_cache import get_token_cache
from backend.services import authorization_service
from backend.services.authorization_service import (
    _build_auth_map,
    _reset_cache_for_tests,
    ensure_model_access,
    grants_access,
    normalize_policy,
)
from backend.services.passthrough_service import ResolvedModel


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _clear_cache():
    """Each test starts with a cold auth-map cache so state doesn't leak."""
    _reset_cache_for_tests()
    yield
    _reset_cache_for_tests()


def _patch_fetch_dnt(data_or_none):
    return patch.object(
        authorization_service, "_fetch_dnt", new=AsyncMock(return_value=data_or_none)
    )


def _patch_no_passthrough():
    """resolve_model → None: the id is not in any provider's namespace, so
    it is forwarded to OpenTela unchanged and is its own DNT key."""
    return patch.object(
        authorization_service, "resolve_model", new=AsyncMock(return_value=None)
    )


def _patch_email(email):
    return patch.object(
        authorization_service, "get_email_for_token", return_value=email
    )


def _dnt_peer(model_id: str, auth_value: str | None) -> dict:
    labels = {"worker_group_id": "wg"}
    if auth_value is not None:
        labels["authorization"] = auth_value
    return {
        "id": "QmPeer",
        "labels": labels,
        "service": [
            {"name": "llm", "identity_group": [f"model={model_id}"]},
        ],
    }


# ── label grammar ───────────────────────────────────────────────────────────


def test_public_and_empty_grant_everyone():
    for value in ("public", "PUBLIC", "", "  "):
        assert grants_access(value, None) is True
        assert grants_access(value, "anyone@epfl.ch") is True


def test_email_list_grants_listed_users_only():
    value = "user1@epfl.ch,user2@ethz.ch"
    assert grants_access(value, "user1@epfl.ch") is True
    assert grants_access(value, "user2@ethz.ch") is True
    assert grants_access(value, "other@epfl.ch") is False
    assert grants_access(value, None) is False


def test_email_match_is_case_insensitive_both_sides():
    """SML normalizes before launch; the backend still compares
    case-insensitively as defense in depth."""
    assert grants_access("User1@EPFL.ch", "user1@epfl.ch") is True
    assert grants_access("user1@epfl.ch", "User1@EPFL.ch") is True


def test_email_list_tolerates_whitespace():
    assert grants_access(" user1@epfl.ch , user2@ethz.ch ", "user2@ethz.ch") is True


def test_normalize_policy_canonicalizes_label_strings():
    """Conflict detection compares policies, not strings: order, case,
    spacing, and the public spellings must all collapse."""
    assert normalize_policy("") is None
    assert normalize_policy("  ") is None
    assert normalize_policy("Public") is None
    assert normalize_policy("a@x.ch, B@Y.ch") == normalize_policy("b@y.ch,a@x.ch")
    assert normalize_policy("a@x.ch") != normalize_policy("a@x.ch,b@y.ch")


# ── DNT → auth map parsing ──────────────────────────────────────────────────


def test_auth_map_reads_identity_group_and_served_model_name_fallback():
    """Ids come from service identity_group entries AND from the
    served_model_name label on pending/follower peers (which carry the
    same labels as their head) — mirrors model_service.get_all_models."""
    data = {
        "/QmHead": _dnt_peer("swiss-ai/Apertus-8B", "user1@epfl.ch"),
        "/QmFollower": {
            "id": "QmFollower",
            "labels": {
                "worker_group_id": "wg",
                "served_model_name": "swiss-ai/Apertus-8B",
                "authorization": "user1@epfl.ch",
            },
            "service": [],
        },
        "/QmPublic": _dnt_peer("meta/Llama", None),
    }
    auth_map = _build_auth_map(data)
    assert auth_map["swiss-ai/Apertus-8B"] == ["user1@epfl.ch", "user1@epfl.ch"]
    assert auth_map["meta/Llama"] == [""]


# ── ensure_model_access decision matrix ─────────────────────────────────────


def test_public_model_allows_any_caller():
    with (
        _patch_no_passthrough(),
        _patch_fetch_dnt({"/p": _dnt_peer("m", "public")}),
        _patch_email("someone@epfl.ch"),
    ):
        _run(ensure_model_access(None, "sk-rc-x", "m"))


def test_unlabeled_model_allows_any_caller():
    """Backward compat: every model launched before this feature has no
    authorization label and must keep working."""
    with (
        _patch_no_passthrough(),
        _patch_fetch_dnt({"/p": _dnt_peer("m", None)}),
        _patch_email("someone@epfl.ch"),
    ):
        _run(ensure_model_access(None, "sk-rc-x", "m"))


def test_listed_email_allows():
    with (
        _patch_no_passthrough(),
        _patch_fetch_dnt({"/p": _dnt_peer("m", "a@epfl.ch,b@ethz.ch")}),
        _patch_email("b@ethz.ch"),
    ):
        _run(ensure_model_access(None, "sk-rc-x", "m"))


def test_listed_email_allows_case_insensitively():
    with (
        _patch_no_passthrough(),
        _patch_fetch_dnt({"/p": _dnt_peer("m", "A@EPFL.ch")}),
        _patch_email("a@epfl.ch"),
    ):
        _run(ensure_model_access(None, "sk-rc-x", "m"))


def test_unlisted_email_denied_with_403():
    with (
        _patch_no_passthrough(),
        _patch_fetch_dnt({"/p": _dnt_peer("m", "a@epfl.ch")}),
        _patch_email("intruder@ethz.ch"),
    ):
        with pytest.raises(HTTPException) as exc_info:
            _run(ensure_model_access(None, "sk-rc-x", "m"))
    assert exc_info.value.status_code == 403
    assert "not authorized" in exc_info.value.detail
    assert "'m'" in exc_info.value.detail


def test_same_policy_across_entries_is_not_a_conflict():
    """Replicas, followers, and consecutive-chain handovers of one launch
    all carry the same label — possibly in a different string form. Order,
    case, spacing, and missing-vs-'public' must all normalize to one
    policy and behave like a single entry."""
    restricted = {
        "/p1": _dnt_peer("m", "a@epfl.ch,B@ETHZ.ch"),
        "/p2": _dnt_peer("m", " b@ethz.ch , a@epfl.ch "),
    }
    with (
        _patch_no_passthrough(),
        _patch_fetch_dnt(restricted),
        _patch_email("b@ethz.ch"),
    ):
        _run(ensure_model_access(None, "sk-rc-x", "m"))

    _reset_cache_for_tests()
    public_forms = {
        "/p1": _dnt_peer("m", None),
        "/p2": _dnt_peer("m", ""),
        "/p3": _dnt_peer("m", "PUBLIC"),
    }
    with (
        _patch_no_passthrough(),
        _patch_fetch_dnt(public_forms),
        _patch_email("anyone@epfl.ch"),
    ):
        _run(ensure_model_access(None, "sk-rc-x", "m"))


def test_conflicting_policies_refuse_everyone():
    """Two launches squatting one served name with different policies:
    OpenTela load-balances across BOTH, so the gateway cannot keep a
    request off the colliding replica. Nobody gets through — not the
    restricted list's owner, and not callers the public entry would
    admit (union semantics would let a same-named public launch widen
    access to a restricted model)."""
    data = {
        "/p1": _dnt_peer("m", "alice@epfl.ch"),
        "/p2": _dnt_peer("m", "public"),
    }
    for caller in ("alice@epfl.ch", "someone@ethz.ch"):
        _reset_cache_for_tests()
        with (
            _patch_no_passthrough(),
            _patch_fetch_dnt(data),
            _patch_email(caller),
        ):
            with pytest.raises(HTTPException) as exc_info:
                _run(ensure_model_access(None, "sk-rc-x", "m"))
        assert exc_info.value.status_code == 403
        assert "conflicting authorization" in exc_info.value.detail


def test_legacy_unlabeled_entry_conflicts_with_a_restricted_one():
    """A pre-feature (unlabeled = public) launch colliding with a new
    restricted launch is the same ambiguity — refuse, don't widen."""
    data = {
        "/p1": _dnt_peer("m", None),
        "/p2": _dnt_peer("m", "alice@epfl.ch"),
    }
    with (
        _patch_no_passthrough(),
        _patch_fetch_dnt(data),
        _patch_email("alice@epfl.ch"),
    ):
        with pytest.raises(HTTPException) as exc_info:
            _run(ensure_model_access(None, "sk-rc-x", "m"))
    assert exc_info.value.status_code == 403
    assert "conflicting authorization" in exc_info.value.detail


def test_two_restricted_launches_with_different_lists_conflict():
    data = {
        "/p1": _dnt_peer("m", "alice@epfl.ch"),
        "/p2": _dnt_peer("m", "alice@epfl.ch,mallory@evil.ch"),
    }
    with (
        _patch_no_passthrough(),
        _patch_fetch_dnt(data),
        _patch_email("mallory@evil.ch"),
    ):
        with pytest.raises(HTTPException) as exc_info:
            _run(ensure_model_access(None, "sk-rc-x", "m"))
    assert exc_info.value.status_code == 403
    assert "conflicting authorization" in exc_info.value.detail


def test_passthrough_model_always_allowed():
    """Synthetic passthrough-provider models (CSCS L1, RCP) are public —
    the DNT is not even consulted."""
    fetch = AsyncMock(return_value={})
    resolved = ResolvedModel(
        provider=object(),
        upstream_id="swiss-ai/Apertus-8B",
        public_id="CSCS-Inference/swiss-ai/Apertus-8B",
    )
    with (
        patch.object(
            authorization_service,
            "resolve_model",
            new=AsyncMock(return_value=resolved),
        ),
        patch.object(authorization_service, "_fetch_dnt", new=fetch),
    ):
        _run(ensure_model_access(None, "sk-rc-x", "CSCS-Inference/swiss-ai/Apertus-8B"))
    assert fetch.await_count == 0


def test_platform_id_is_checked_under_the_id_that_gets_routed():
    """Since #122 a SwissAI-Research/ id is forwarded verbatim — the full
    prefixed id is the k8s deployment's served name — so the policy must be
    read from the DNT under that same id."""
    resolved = ResolvedModel(
        provider=None,
        upstream_id="SwissAI-Research/org/m",
        public_id="SwissAI-Research/org/m",
    )
    with (
        patch.object(
            authorization_service,
            "resolve_model",
            new=AsyncMock(return_value=resolved),
        ),
        _patch_fetch_dnt({"/p": _dnt_peer("SwissAI-Research/org/m", "owner@epfl.ch")}),
        _patch_email("outsider@epfl.ch"),
    ):
        with pytest.raises(HTTPException) as exc_info:
            _run(ensure_model_access(None, "sk-rc-x", "SwissAI-Research/org/m"))
    assert exc_info.value.status_code == 403


def test_a_rewritten_upstream_id_is_still_checked():
    """Guard for the day id rewriting comes back: if the proxy forwards a
    different id than the caller asked for, a policy on the forwarded id
    must still bind, or the public alias becomes a way around it."""
    resolved = ResolvedModel(
        provider=None,
        upstream_id="org/m",
        public_id="SwissAI-Research/org/m",
    )
    with (
        patch.object(
            authorization_service,
            "resolve_model",
            new=AsyncMock(return_value=resolved),
        ),
        _patch_fetch_dnt({"/p": _dnt_peer("org/m", "owner@epfl.ch")}),
        _patch_email("outsider@epfl.ch"),
    ):
        with pytest.raises(HTTPException) as exc_info:
            _run(ensure_model_access(None, "sk-rc-x", "SwissAI-Research/org/m"))
    assert exc_info.value.status_code == 403


def test_unknown_model_allowed():
    """An id the DNT doesn't know falls through to upstream, which 404s —
    unchanged behavior."""
    with (
        _patch_no_passthrough(),
        _patch_fetch_dnt({"/p": _dnt_peer("other", "a@epfl.ch")}),
        _patch_email(None),
    ):
        _run(ensure_model_access(None, "sk-rc-x", "unknown"))


def test_cold_start_dnt_failure_fails_open(caplog):
    """DNT unreachable and nothing cached yet → allow, don't 500 or 403 —
    but leave a warning in the logs."""
    with (
        _patch_no_passthrough(),
        _patch_fetch_dnt(None),
        caplog.at_level("WARNING", logger="backend"),
    ):
        _run(ensure_model_access(None, "sk-rc-x", "m"))
    assert any("fail open" in r.message for r in caplog.records)


def test_stale_cache_still_enforced_after_fetch_failure():
    """Once a map was fetched, a later DNT outage keeps enforcing from the
    stale map instead of silently opening restricted models up."""
    fake = AsyncMock(side_effect=[{"/p": _dnt_peer("m", "a@epfl.ch")}, None])
    with (
        _patch_no_passthrough(),
        patch.object(authorization_service, "_fetch_dnt", new=fake),
        _patch_email("intruder@ethz.ch"),
    ):
        with pytest.raises(HTTPException):
            _run(ensure_model_access(None, "sk-rc-x", "m"))
        # Expire the freshness sentinel; the next refresh attempt fails.
        get_token_cache().invalidate_auth_map_freshness()
        with pytest.raises(HTTPException) as exc_info:
            _run(ensure_model_access(None, "sk-rc-x", "m"))
    assert exc_info.value.status_code == 403
    assert fake.await_count == 2


def test_auth_map_cached_within_ttl():
    """Successive checks within the TTL reuse the cached map — the DNT is
    not fetched per request on the inference hot path."""
    fake = AsyncMock(return_value={"/p": _dnt_peer("m", "public")})
    with (
        _patch_no_passthrough(),
        patch.object(authorization_service, "_fetch_dnt", new=fake),
        _patch_email(None),
    ):
        _run(ensure_model_access(None, "sk-rc-x", "m"))
        _run(ensure_model_access(None, "sk-rc-x", "m"))
    assert fake.await_count == 1
    assert get_token_cache().auth_map_is_fresh()


def test_auth_map_lives_in_the_shared_cache_not_the_module():
    """The map is stored in the token cache, so every serving-api replica
    reads the same one instead of each keeping (and cold-starting) its own."""
    with (
        _patch_no_passthrough(),
        _patch_fetch_dnt({"/p": _dnt_peer("m", "a@epfl.ch")}),
        _patch_email("a@epfl.ch"),
    ):
        _run(ensure_model_access(None, "sk-rc-x", "m"))
    assert get_token_cache().get_auth_map() == {"m": ["a@epfl.ch"]}


def test_map_another_replica_cached_is_used_without_fetching():
    """A replica that never fetched anything itself still enforces from the
    map a peer replica put in the shared cache — no cold-start fail-open of
    its own, and no duplicate DNT fetch."""
    cache = get_token_cache()
    cache.set_auth_map({"m": ["a@epfl.ch"]}, ttl=60)
    cache.mark_auth_map_fetched(ttl=10)

    fake = AsyncMock(return_value=None)
    with (
        _patch_no_passthrough(),
        patch.object(authorization_service, "_fetch_dnt", new=fake),
        _patch_email("intruder@ethz.ch"),
    ):
        with pytest.raises(HTTPException) as exc_info:
            _run(ensure_model_access(None, "sk-rc-x", "m"))
    assert exc_info.value.status_code == 403
    assert fake.await_count == 0


def test_failed_refresh_still_backs_off():
    """A failed refresh marks the attempt too, so a DNT outage costs one
    fetch timeout per interval rather than one per inference request."""
    fake = AsyncMock(return_value=None)
    with (
        _patch_no_passthrough(),
        patch.object(authorization_service, "_fetch_dnt", new=fake),
        _patch_email(None),
    ):
        _run(ensure_model_access(None, "sk-rc-x", "m"))
        _run(ensure_model_access(None, "sk-rc-x", "m"))
    assert fake.await_count == 1


def test_expired_stale_map_falls_back_to_fail_open(caplog):
    """The stale map is servable for a bounded window, not forever: once it
    expires with the DNT still down we are back at cold start."""
    cache = get_token_cache()
    cache.set_auth_map({"m": ["a@epfl.ch"]}, ttl=0)

    with (
        _patch_no_passthrough(),
        _patch_fetch_dnt(None),
        _patch_email("intruder@ethz.ch"),
        caplog.at_level("WARNING", logger="backend"),
    ):
        _run(ensure_model_access(None, "sk-rc-x", "m"))
    assert any("fail open" in r.message for r in caplog.records)


# ── shared-cache primitives ─────────────────────────────────────────────────


def test_invalidating_freshness_keeps_the_stale_map():
    """Forcing a refresh must not open a fail-open window in the meantime:
    the sentinel goes, the data stays."""
    cache = get_token_cache()
    cache.set_auth_map({"m": ["a@epfl.ch"]}, ttl=60)
    cache.mark_auth_map_fetched(ttl=60)

    cache.invalidate_auth_map_freshness()

    assert cache.auth_map_is_fresh() is False
    assert cache.get_auth_map() == {"m": ["a@epfl.ch"]}


def test_clear_auth_map_drops_both_data_and_sentinel():
    cache = get_token_cache()
    cache.set_auth_map({"m": ["a@epfl.ch"]}, ttl=60)
    cache.mark_auth_map_fetched(ttl=60)

    cache.clear_auth_map()

    assert cache.auth_map_is_fresh() is False
    assert cache.get_auth_map() is None


def test_auth_map_cache_does_not_disturb_identities():
    """The three caches share a client but not a namespace — clearing the
    map must not log every user back out."""
    cache = get_token_cache()
    cache.set_email("sk-rc-x", "a@epfl.ch", ttl=60)
    cache.set_auth_map({"m": ["a@epfl.ch"]}, ttl=60)

    cache.clear_auth_map()

    assert cache.get_email("sk-rc-x") == "a@epfl.ch"


# ── fixture-mode DNT source ─────────────────────────────────────────────────


def test_fetch_dnt_reads_fixture_file(tmp_path):
    """OTELA_FIXTURE_PATH set → the auth map reads the same on-disk JSON
    the models router serves, not HTTP."""
    fixture = tmp_path / "dnt.json"
    fixture.write_text('{"/p": {"labels": {}, "service": []}}')

    class S:
        otela_fixture_path = str(fixture)
        otela_head_addr = "http://unused"

    with patch.object(authorization_service, "get_settings", return_value=S()):
        data = _run(authorization_service._fetch_dnt())
    assert data == {"/p": {"labels": {}, "service": []}}
