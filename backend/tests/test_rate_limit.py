"""Unit tests for the per-user rate limiter — sliding-window math,
override precedence, fail-open behavior, and the 429 surface (envelope +
Retry-After headers) through the middleware dependency."""

from unittest.mock import patch

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException

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


@pytest.fixture()
def fake_redis():
    client = FakeRedis()
    with patch.object(
        rate_limit_service,
        "get_token_cache",
        return_value=_FakeTokenCache(client),
    ):
        yield client


def _settings_rpm(rpm: int):
    class _S:
        rate_limit_rpm = rpm

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


# ── 429 surface through the dependency ──────────────────────────────────────


def _make_app() -> FastAPI:
    from backend.main import http_exception_handler
    from backend.middleware import ratelimit
    from backend.middleware.auth import require_auth

    app = FastAPI()
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)

    async def fake_auth() -> str:
        return "tok"

    app.dependency_overrides[require_auth] = fake_auth

    @app.post("/limited")
    async def limited(token: str = Depends(ratelimit.rate_limited)):
        return {"ok": True, "token": token}

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


def test_dependency_returns_token_when_allowed(fake_redis):
    with _settings_rpm(10):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.post("/limited")
    assert resp.status_code == 200
    assert resp.json()["token"] == "tok"
