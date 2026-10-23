"""Unit tests for the per-user rate limiter — sliding-window math,
override precedence, fail-open behavior, and the 429 surface (envelope +
Retry-After headers) through the middleware dependency. The durable
per-user limit (apikey.rate_limit_rpm) and its admin API run against the
shared migrated Postgres from conftest.py."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, select
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.models.entities import APIKey
from backend.services import rate_limit_service
from backend.services.rate_limit_service import (
    WINDOW_SECONDS,
    check_rate_limit,
)


class FakeRedis:
    """Minimal sync-redis stand-in: string values, INCR/EXPIRE/GET, and a
    pipeline that replays those calls in order on execute()."""

    def __init__(self):
        self.store = {}
        self.fail = False

    # -- direct API used by tests to seed state --------------------------
    def set(self, key, value):
        self.store[key] = str(value)

    # -- pipeline protocol ------------------------------------------------
    def pipeline(self):
        return _FakePipeline(self)

    def incr(self, key):
        value = int(self.store.get(key, 0)) + 1
        self.store[key] = str(value)
        return value

    def expire(self, key, ttl):
        return True

    def get(self, key):
        return self.store.get(key)


class _FakePipeline:
    def __init__(self, client):
        self.client = client
        self.ops = []

    def incr(self, key):
        self.ops.append(("incr", key))
        return self

    def expire(self, key, ttl):
        self.ops.append(("expire", key, ttl))
        return self

    def get(self, key):
        self.ops.append(("get", key))
        return self

    def execute(self):
        if self.client.fail:
            raise ConnectionError("redis down")
        results = []
        for op in self.ops:
            if op[0] == "incr":
                results.append(self.client.incr(op[1]))
            elif op[0] == "expire":
                results.append(self.client.expire(op[1], op[2]))
            else:
                results.append(self.client.get(op[1]))
        return results


class _FakeTokenCache:
    def __init__(self, client):
        self.redis_client = client


@pytest.fixture(autouse=True)
def no_db_limits():
    """No per-user DB limit unless a test wires in Postgres (db_limits), so
    the Redis-only tests never depend on a database being reachable."""
    with patch.object(rate_limit_service, "_get_engine", return_value=None):
        yield
    rate_limit_service._db_limit_cache.clear()


@pytest.fixture()
def fake_redis():
    client = FakeRedis()
    with patch.object(
        rate_limit_service,
        "get_token_cache",
        return_value=_FakeTokenCache(client),
    ):
        yield client


def _settings_rpm(rpm: int, chat_api_keys: str = ""):
    class _S:
        rate_limit_rpm = rpm

    _S.chat_api_keys = chat_api_keys

    return patch.object(rate_limit_service, "get_settings", return_value=_S())


# Window-aligned timestamp so weighted-count math is exact in tests.
T0 = 1_000_000 * WINDOW_SECONDS


# ── decision math ───────────────────────────────────────────────────────────


def test_under_limit_allowed_with_remaining(fake_redis):
    with _settings_rpm(10):
        decision = check_rate_limit("tok", now=T0)
    assert decision.allowed
    assert decision.limit == 10
    assert decision.remaining == 9


def test_requests_over_limit_rejected_with_retry_after(fake_redis):
    with _settings_rpm(3):
        for _ in range(3):
            assert check_rate_limit("tok", now=T0).allowed
        denied = check_rate_limit("tok", now=T0 + 30)
    assert not denied.allowed
    assert denied.limit == 3
    assert denied.remaining == 0
    assert 1 <= denied.retry_after <= WINDOW_SECONDS


def test_previous_window_decays(fake_redis):
    """Sliding window: last minute's traffic counts, weighted by overlap.
    10 requests in the previous window at 30s elapsed weigh as 5."""
    with _settings_rpm(8):
        window = int((T0 + 30) // WINDOW_SECONDS)
        fake_redis.set(f"rl:req:{rate_limit_service._identity('tok')}:{window - 1}", 10)
        # weighted = 10 * 0.5 + 1 = 6 <= 8 → allowed
        allowed = check_rate_limit("tok", now=T0 + 30)
        assert allowed.allowed
        # three more requests: weighted = 5 + 4 = 9 > 8 → denied
        check_rate_limit("tok", now=T0 + 30)
        check_rate_limit("tok", now=T0 + 30)
        denied = check_rate_limit("tok", now=T0 + 30)
    assert not denied.allowed


def test_rejected_requests_still_count(fake_redis):
    """429s must not reset the caller's budget — the counter increments
    on every attempt, so hammering stays limited."""
    with _settings_rpm(1):
        check_rate_limit("tok", now=T0)
        for _ in range(3):
            assert not check_rate_limit("tok", now=T0 + 1).allowed
    ident = rate_limit_service._identity("tok")
    window = int(T0 // WINDOW_SECONDS)
    assert fake_redis.store[f"rl:req:{ident}:{window}"] == "4"


def test_distinct_tokens_do_not_share_budget(fake_redis):
    with _settings_rpm(1):
        assert check_rate_limit("tok-a", now=T0).allowed
        assert check_rate_limit("tok-b", now=T0).allowed


# ── limit resolution ────────────────────────────────────────────────────────


def test_disabled_when_no_limit_configured(fake_redis):
    with _settings_rpm(0):
        for _ in range(50):
            decision = check_rate_limit("tok", now=T0)
    assert decision.allowed


def test_default_override_enables_without_env(fake_redis):
    """rl:limit:default turns limiting on even with RATE_LIMIT_RPM unset —
    the no-redeploy admin path."""
    fake_redis.set("rl:limit:default", 2)
    with _settings_rpm(0):
        check_rate_limit("tok", now=T0)
        check_rate_limit("tok", now=T0)
        denied = check_rate_limit("tok", now=T0 + 1)
    assert not denied.allowed
    assert denied.limit == 2


def test_user_override_beats_default_and_env(fake_redis):
    fake_redis.set("rl:limit:default", 1)
    fake_redis.set(f"rl:limit:{rate_limit_service._identity('vip')}", 100)
    with _settings_rpm(1):
        check_rate_limit("vip", now=T0)
        decision = check_rate_limit("vip", now=T0)
    assert decision.allowed
    assert decision.limit == 100


def test_user_override_zero_means_unlimited(fake_redis):
    fake_redis.set("rl:limit:default", 1)
    fake_redis.set(f"rl:limit:{rate_limit_service._identity('vip')}", 0)
    with _settings_rpm(1):
        check_rate_limit("vip", now=T0)
        assert check_rate_limit("vip", now=T0).allowed


def test_malformed_override_falls_through(fake_redis):
    fake_redis.set(f"rl:limit:{rate_limit_service._identity('tok')}", "banana")
    fake_redis.set("rl:limit:default", 1)
    with _settings_rpm(5):
        check_rate_limit("tok", now=T0)
        denied = check_rate_limit("tok", now=T0)
    assert not denied.allowed
    assert denied.limit == 1


# ── fail-open ───────────────────────────────────────────────────────────────


def test_redis_error_fails_open(fake_redis):
    fake_redis.fail = True
    with _settings_rpm(1):
        for _ in range(5):
            assert check_rate_limit("tok", now=T0).allowed


def test_no_redis_client_fails_open():
    with (
        patch.object(
            rate_limit_service,
            "get_token_cache",
            return_value=_FakeTokenCache(None),
        ),
        _settings_rpm(1),
    ):
        assert check_rate_limit("tok", now=T0).allowed


# ── 429 surface + passthrough-only scope ────────────────────────────────────


def _make_app() -> FastAPI:
    from backend.main import http_exception_handler
    from backend.middleware.ratelimit import enforce_rate_limit

    app = FastAPI()
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)

    @app.post("/limited")
    async def limited():
        enforce_rate_limit("tok")
        return {"ok": True}

    return app


def test_429_envelope_and_headers(fake_redis):
    with _settings_rpm(1):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        assert client.post("/limited").status_code == 200
        resp = client.post("/limited")
    assert resp.status_code == 429
    err = resp.json()["error"]
    assert err["type"] == "rate_limit_error"
    assert "requests per minute" in err["message"]
    assert resp.headers["Retry-After"].isdigit()
    assert resp.headers["X-RateLimit-Limit"] == "1"
    assert resp.headers["X-RateLimit-Remaining"] == "0"


def test_passthrough_routing_enforces_limit(fake_redis):
    """Requests resolving to an external provider count against the
    budget and 429 once it's spent."""
    from fastapi import HTTPException

    from backend.services import route_service
    from backend.services.passthrough_service import Provider, ResolvedModel

    provider = Provider(
        name="cscs_L1",
        base_url="https://l1/v1",
        api_key="pk",
        device="CSCS L1",
        prefix="CSCS-Inference",
    )
    resolved = ResolvedModel(
        provider=provider,
        upstream_id="swiss-ai/x",
        public_id="CSCS-Inference/swiss-ai/x",
    )
    with (
        patch.object(
            route_service, "resolve_model", new=AsyncMock(return_value=resolved)
        ),
        _settings_rpm(1),
    ):
        route = asyncio.run(
            route_service.resolve_route("CSCS-Inference/swiss-ai/x", "tok")
        )
        assert (route.endpoint, route.api_key, route.provider_label) == (
            "https://l1/v1",
            "pk",
            "CSCS L1",
        )
        assert route.resolved is resolved
        with pytest.raises(HTTPException) as exc:
            asyncio.run(route_service.resolve_route("CSCS-Inference/swiss-ai/x", "tok"))
    assert exc.value.status_code == 429


def test_opentela_routing_is_never_limited(fake_redis):
    """Locally-served models run on the user's own GPU allocation: no
    budget is consumed and no 429 is possible, however many requests."""
    from backend.services import route_service

    with (
        patch.object(route_service, "resolve_model", new=AsyncMock(return_value=None)),
        _settings_rpm(1),
    ):
        for _ in range(5):
            route = asyncio.run(route_service.resolve_route("some/local-model", "tok"))
    assert route.provider_label is None
    assert route.resolved is None
    assert route.api_key == "tok"
    # No counter was ever touched for this caller.
    ident = rate_limit_service._identity("tok")
    assert not any(k.startswith(f"rl:req:{ident}") for k in fake_redis.store)


def test_platform_namespace_is_never_limited(fake_redis):
    """SwissAI-Research/... resolves to our own OpenTela network — same
    no-limit treatment as bare ids, but the resolution is returned so the
    routes still rewrite ids."""
    from backend.services import route_service
    from backend.services.passthrough_service import ResolvedModel

    resolved = ResolvedModel(
        provider=None,
        upstream_id="some/local-model",
        public_id="SwissAI-Research/some/local-model",
    )
    with (
        patch.object(
            route_service, "resolve_model", new=AsyncMock(return_value=resolved)
        ),
        _settings_rpm(1),
    ):
        for _ in range(5):
            route = asyncio.run(
                route_service.resolve_route("SwissAI-Research/some/local-model", "tok")
            )
    assert route.provider_label is None
    assert route.resolved is resolved
    assert route.api_key == "tok"
    ident = rate_limit_service._identity("tok")
    assert not any(k.startswith(f"rl:req:{ident}") for k in fake_redis.store)


# ── chat keys (CHAT_API_KEYS) ───────────────────────────────────────────────


def test_chat_key_is_never_limited_or_counted(fake_redis):
    from backend.middleware.ratelimit import enforce_rate_limit

    with _settings_rpm(1, chat_api_keys="chat-key"):
        for _ in range(20):
            enforce_rate_limit("chat-key")
    ident = rate_limit_service._identity("chat-key")
    assert not any(k.startswith(f"rl:req:{ident}") for k in fake_redis.store)


def test_chat_key_beats_redis_overrides(fake_redis):
    """Even an admin's Redis limit can't re-throttle chat."""
    fake_redis.set("rl:limit:default", 1)
    fake_redis.set(f"rl:limit:{rate_limit_service._identity('chat-key')}", 1)
    from backend.middleware.ratelimit import enforce_rate_limit

    with _settings_rpm(1, chat_api_keys="chat-key"):
        for _ in range(5):
            enforce_rate_limit("chat-key")


def test_other_keys_still_limited_alongside_chat(fake_redis):
    from fastapi import HTTPException

    from backend.middleware.ratelimit import enforce_rate_limit

    with _settings_rpm(1, chat_api_keys="chat-key"):
        enforce_rate_limit("user-key")
        with pytest.raises(HTTPException) as exc:
            enforce_rate_limit("user-key")
    assert exc.value.status_code == 429


@pytest.mark.parametrize(
    "configured", ["chat-a,chat-b", " chat-a , chat-b ", "chat-b,,chat-a,"]
)
def test_chat_api_keys_parsing(configured):
    """Comma-separated, whitespace and empty entries tolerated — prod and
    dev chat keys can share one setting."""
    with _settings_rpm(1, chat_api_keys=configured):
        assert rate_limit_service.is_exempt("chat-a")
        assert rate_limit_service.is_exempt("chat-b")
        assert not rate_limit_service.is_exempt("chat")
        assert not rate_limit_service.is_exempt("")


def test_no_chat_keys_configured_exempts_nobody():
    with _settings_rpm(1):
        assert not rate_limit_service.is_exempt("chat-key")
        assert not rate_limit_service.is_exempt("")


def test_passthrough_routing_never_limits_chat(fake_redis):
    """The chat key reaches a passthrough provider however often it calls —
    the exemption applies on the routing path, not just in isolation."""
    from backend.services import route_service
    from backend.services.passthrough_service import Provider, ResolvedModel

    provider = Provider(
        name="cscs_L1",
        base_url="https://l1/v1",
        api_key="pk",
        device="CSCS L1",
        prefix="CSCS-Inference",
    )
    resolved = ResolvedModel(
        provider=provider,
        upstream_id="swiss-ai/x",
        public_id="CSCS-Inference/swiss-ai/x",
    )
    with (
        patch.object(
            route_service, "resolve_model", new=AsyncMock(return_value=resolved)
        ),
        _settings_rpm(1, chat_api_keys="chat-key"),
    ):
        for _ in range(5):
            route = asyncio.run(
                route_service.resolve_route("CSCS-Inference/swiss-ai/x", "chat-key")
            )
    assert route.endpoint == "https://l1/v1"


# ── durable per-user limit (apikey.rate_limit_rpm) ──────────────────────────


@pytest.fixture()
def db_limits(engine):
    """Wire the migrated test DB into the limiter; drop the test keys after."""
    with patch.object(rate_limit_service, "_get_engine", return_value=engine):
        yield engine
    with Session(engine) as session:
        for row in session.exec(
            select(APIKey).where(APIKey.owner_email.like("%@rl.test"))
        ).all():
            session.delete(row)
        session.commit()
    rate_limit_service._db_limit_cache.clear()


def _make_key(engine, key, email, rpm=None):
    with Session(engine) as session:
        session.add(APIKey(key=key, owner_email=email, rate_limit_rpm=rpm))
        session.commit()


def test_db_limit_applies_without_redis_override(fake_redis, db_limits):
    _make_key(db_limits, "sk-rc-rl-a", "a@rl.test", rpm=2)
    with _settings_rpm(1):
        check_rate_limit("sk-rc-rl-a", now=T0)
        check_rate_limit("sk-rc-rl-a", now=T0)
        denied = check_rate_limit("sk-rc-rl-a", now=T0)
    assert not denied.allowed
    assert denied.limit == 2


def test_db_limit_beats_default_override_and_env(fake_redis, db_limits):
    fake_redis.set("rl:limit:default", 1)
    _make_key(db_limits, "sk-rc-rl-b", "b@rl.test", rpm=500)
    with _settings_rpm(1):
        check_rate_limit("sk-rc-rl-b", now=T0)
        decision = check_rate_limit("sk-rc-rl-b", now=T0)
    assert decision.allowed
    assert decision.limit == 500


def test_redis_user_override_beats_db_limit(fake_redis, db_limits):
    """The Redis per-user key stays the instant hotfix on top."""
    _make_key(db_limits, "sk-rc-rl-c", "c@rl.test", rpm=500)
    fake_redis.set(f"rl:limit:{rate_limit_service._identity('sk-rc-rl-c')}", 1)
    with _settings_rpm(15):
        check_rate_limit("sk-rc-rl-c", now=T0)
        denied = check_rate_limit("sk-rc-rl-c", now=T0)
    assert not denied.allowed
    assert denied.limit == 1


def test_db_limit_zero_means_unlimited(fake_redis, db_limits):
    _make_key(db_limits, "sk-rc-rl-d", "d@rl.test", rpm=0)
    with _settings_rpm(1):
        for _ in range(5):
            assert check_rate_limit("sk-rc-rl-d", now=T0).allowed


def test_db_limit_survives_key_rotation(fake_redis, db_limits):
    """A Redis override hangs off the old key's hash and is orphaned by a
    rotation; the DB limit lives on the row rotate_key rewrites in place."""
    from backend.services import auth_service

    _make_key(db_limits, "sk-rc-rl-e", "e@rl.test", rpm=500)
    with patch.object(auth_service, "get_token_cache", return_value=MagicMock()):
        new_key = auth_service.rotate_key(db_limits, "sk-rc-rl-e").key
    assert new_key != "sk-rc-rl-e"
    with _settings_rpm(1):
        check_rate_limit(new_key, now=T0)
        decision = check_rate_limit(new_key, now=T0)
    assert decision.allowed
    assert decision.limit == 500


def test_set_user_limit_applies_at_once_on_this_replica(fake_redis, db_limits):
    """Writing drops the ~60s lookup cache, so the replica that served the
    admin call doesn't keep enforcing the old limit."""
    _make_key(db_limits, "sk-rc-rl-f", "f@rl.test")
    with _settings_rpm(1):
        assert check_rate_limit("sk-rc-rl-f", now=T0).limit == 1
        assert rate_limit_service.set_user_limit(db_limits, "f@rl.test", 500) == 1
        assert check_rate_limit("sk-rc-rl-f", now=T0).limit == 500
    assert rate_limit_service.set_user_limit(db_limits, "nobody@rl.test", 500) == 0


def test_db_lookup_failure_falls_back_to_default(fake_redis):
    with (
        patch.object(rate_limit_service, "_get_engine", return_value=object()),
        patch.object(rate_limit_service, "Session", side_effect=RuntimeError("down")),
        _settings_rpm(1),
    ):
        check_rate_limit("tok", now=T0)
        denied = check_rate_limit("tok", now=T0)
    assert not denied.allowed
    assert denied.limit == 1


# ── admin API ───────────────────────────────────────────────────────────────


def test_admin_rate_limit_roundtrip(client, db_limits):
    from backend.routers.admin_monitoring import require_admin

    _make_key(db_limits, "sk-rc-rl-g", "g@rl.test")
    client.app.dependency_overrides[require_admin] = lambda: "admin@test.ch"
    try:
        missing = client.put("/v1/admin/rate-limits/nobody@rl.test", json={"rpm": 5})
        assert missing.status_code == 404
        negative = client.put("/v1/admin/rate-limits/g@rl.test", json={"rpm": -1})
        assert negative.status_code == 422

        set_resp = client.put("/v1/admin/rate-limits/g@rl.test", json={"rpm": 500})
        assert set_resp.status_code == 200
        assert set_resp.json() == {"owner_email": "g@rl.test", "rate_limit_rpm": 500}
        listed = client.get("/v1/admin/rate-limits").json()["users"]
        assert {"owner_email": "g@rl.test", "rate_limit_rpm": 500} in listed

        cleared = client.delete("/v1/admin/rate-limits/g@rl.test")
        assert cleared.json() == {"owner_email": "g@rl.test", "rate_limit_rpm": None}
        listed = client.get("/v1/admin/rate-limits").json()["users"]
        assert all(u["owner_email"] != "g@rl.test" for u in listed)
    finally:
        client.app.dependency_overrides.pop(require_admin, None)


def test_admin_rate_limit_requires_admin(client, db_limits):
    _make_key(db_limits, "sk-rc-rl-h", "h@rl.test")
    resp = client.put(
        "/v1/admin/rate-limits/h@rl.test",
        json={"rpm": 500},
        headers={"Authorization": "Bearer sk-rc-rl-h"},
    )
    assert resp.status_code == 403
