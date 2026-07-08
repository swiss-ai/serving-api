"""Tests for the AUTH_PROVIDER toggle (issue #58).

A single env var selects the active IdP while both credential sets can be
present at once, so a prod cutover/rollback is a one-line env change. The
backend trusts only the active issuer — a flip forces re-login.
"""

from types import SimpleNamespace

import pytest

from backend.config import Settings
from backend.services import auth_service

AUTH0 = "https://auth0.example.com/"
AUTHENTIK = "https://authentik.example.com/application/o/app/"


def _settings(**kwargs):
    base = dict(
        dev_auth_bypass=False,
        database_url="postgresql://user:pw@remote/db",
    )
    base.update(kwargs)
    # _env_file=None keeps the test independent of any developer-local .env.
    return Settings(_env_file=None, **base)


def test_default_provider_is_auth0():
    s = _settings(auth0_issuer=AUTH0)
    assert s.auth_provider == "auth0"
    assert s.active_issuer() == AUTH0


def test_authentik_provider_selects_authentik_issuer():
    s = _settings(
        auth_provider="authentik", auth0_issuer=AUTH0, authentik_issuer=AUTHENTIK
    )
    assert s.active_issuer() == AUTHENTIK


def test_authentik_falls_back_to_auth0_names_when_unset():
    """Env that holds Authentik values under the legacy AUTH0_* names keeps
    working by only flipping AUTH_PROVIDER."""
    s = _settings(
        auth_provider="authentik", auth0_issuer=AUTHENTIK, authentik_issuer=""
    )
    assert s.active_issuer() == AUTHENTIK


def test_profile_uses_only_active_issuer(monkeypatch):
    monkeypatch.setattr(
        auth_service,
        "get_settings",
        lambda: _settings(
            auth_provider="authentik",
            auth0_issuer=AUTH0,
            authentik_issuer=AUTHENTIK,
        ),
    )
    monkeypatch.setattr(
        auth_service,
        "get_userinfo_endpoint",
        lambda issuer: f"{issuer}userinfo",
    )

    calls = []

    def fake_get(url, headers=None, **kwargs):
        calls.append(url)
        return SimpleNamespace(
            status_code=200, json=lambda: {"email": "user@example.com"}
        )

    monkeypatch.setattr(auth_service.requests, "get", fake_get)

    profile = auth_service.get_profile_from_accesstoken("some-token")
    assert profile["email"] == "user@example.com"
    assert len(calls) == 1
    assert calls[0].startswith(AUTHENTIK)


def test_profile_rejects_token_from_previous_issuer_after_flip(monkeypatch):
    """After AUTH_PROVIDER flips to authentik, an Auth0-minted token fails —
    no dual-issuer acceptance; user must re-login."""
    monkeypatch.setattr(
        auth_service,
        "get_settings",
        lambda: _settings(
            auth_provider="authentik",
            auth0_issuer=AUTH0,
            authentik_issuer=AUTHENTIK,
        ),
    )
    monkeypatch.setattr(
        auth_service,
        "get_userinfo_endpoint",
        lambda issuer: f"{issuer}userinfo",
    )
    monkeypatch.setattr(
        auth_service.requests,
        "get",
        lambda url, headers=None, **kwargs: SimpleNamespace(status_code=401, text="unauthorized"),
    )

    with pytest.raises(Exception, match="Invalid access token"):
        auth_service.get_profile_from_accesstoken("auth0-era-token")


def test_profile_rejected_when_active_issuer_rejects(monkeypatch):
    monkeypatch.setattr(
        auth_service,
        "get_settings",
        lambda: _settings(
            auth_provider="auth0", auth0_issuer=AUTH0, authentik_issuer=AUTHENTIK
        ),
    )
    monkeypatch.setattr(
        auth_service,
        "get_userinfo_endpoint",
        lambda issuer: f"{issuer}userinfo",
    )
    monkeypatch.setattr(
        auth_service.requests,
        "get",
        lambda url, headers=None, **kwargs: SimpleNamespace(status_code=401, text="nope"),
    )

    with pytest.raises(Exception):
        auth_service.get_profile_from_accesstoken("bad-token")
