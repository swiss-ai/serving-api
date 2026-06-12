"""Security tests for the local dev auth bypass.

The bypass lets `make auth-run` skip Auth0 for a fixed dev token. These tests
pin down that it stays inert unless explicitly enabled AND running against a
local database, so it can't weaken auth in a deployed environment.
"""

from types import SimpleNamespace

import pytest

from backend.services import auth_service


def _settings(*, dev_auth_bypass, database_url):
    return SimpleNamespace(dev_auth_bypass=dev_auth_bypass, database_url=database_url)


def test_bypass_off_by_default(monkeypatch):
    monkeypatch.setattr(
        auth_service,
        "get_settings",
        lambda: _settings(
            dev_auth_bypass=False, database_url="postgresql://localhost/x"
        ),
    )
    assert auth_service.dev_bypass_enabled() is False


def test_bypass_requires_local_db(monkeypatch):
    """Flag on but a remote DB (i.e. a deployed env) → bypass stays off."""
    monkeypatch.setattr(
        auth_service,
        "get_settings",
        lambda: _settings(
            dev_auth_bypass=True,
            database_url="postgresql://user:pw@prod-neon.example.com/db",
        ),
    )
    assert auth_service.dev_bypass_enabled() is False


def test_bypass_enabled_when_local_and_flagged(monkeypatch):
    monkeypatch.setattr(
        auth_service,
        "get_settings",
        lambda: _settings(
            dev_auth_bypass=True,
            database_url="postgresql://serving:serving@localhost:5433/serving",
        ),
    )
    assert auth_service.dev_bypass_enabled() is True


def test_bypass_returns_dev_profile_without_calling_auth0(monkeypatch):
    monkeypatch.setattr(
        auth_service,
        "get_settings",
        lambda: _settings(
            dev_auth_bypass=True,
            database_url="postgresql://localhost:5433/serving",
        ),
    )

    def boom(*args, **kwargs):
        raise AssertionError("Auth0 must not be called when the bypass is active")

    monkeypatch.setattr(auth_service.requests, "get", boom)

    profile = auth_service.get_profile_from_accesstoken(auth_service.DEV_DUMMY_TOKEN)
    assert profile["email"] == auth_service.DEV_EMAIL


def test_dummy_token_hits_auth0_when_bypass_disabled(monkeypatch):
    """With the flag off, the dummy token is NOT special — it must go to Auth0
    like any other token (and be rejected)."""
    monkeypatch.setattr(
        auth_service,
        "get_settings",
        lambda: _settings(
            dev_auth_bypass=False, database_url="postgresql://localhost/x"
        ),
    )
    called = {}

    def fake_get(url, headers):
        called["url"] = url
        return SimpleNamespace(status_code=401, text="unauthorized")

    monkeypatch.setattr(auth_service.requests, "get", fake_get)

    with pytest.raises(Exception):
        auth_service.get_profile_from_accesstoken(auth_service.DEV_DUMMY_TOKEN)
    assert "userinfo" in called["url"]


def test_dummy_token_hits_auth0_when_db_nonlocal(monkeypatch):
    """Defense-in-depth: flag on but remote DB → dummy token still hits Auth0."""
    monkeypatch.setattr(
        auth_service,
        "get_settings",
        lambda: _settings(
            dev_auth_bypass=True,
            database_url="postgresql://user:pw@prod-neon.example.com/db",
        ),
    )
    called = {}

    def fake_get(url, headers):
        called["url"] = url
        return SimpleNamespace(status_code=401, text="unauthorized")

    monkeypatch.setattr(auth_service.requests, "get", fake_get)

    with pytest.raises(Exception):
        auth_service.get_profile_from_accesstoken(auth_service.DEV_DUMMY_TOKEN)
    assert "userinfo" in called["url"]
