"""Tests for the AUTH_PROVIDER toggle (issue #58).

A single env var selects the active IdP while both credential sets can be
present at once, so a prod cutover/rollback is a one-line env change. The
backend also accepts tokens minted by the *other* configured issuer during a
flip so in-flight sessions survive.
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
    assert s.fallback_issuer() == AUTH0


def test_authentik_falls_back_to_auth0_names_when_unset():
    """Env that holds Authentik values under the legacy AUTH0_* names keeps
    working by only flipping AUTH_PROVIDER."""
    s = _settings(auth_provider="authentik", auth0_issuer=AUTHENTIK, authentik_issuer="")
    assert s.active_issuer() == AUTHENTIK


def test_candidate_issuers_tries_active_then_other():
    s = _settings(
        auth_provider="auth0", auth0_issuer=AUTH0, authentik_issuer=AUTHENTIK
    )
    assert s.candidate_issuers() == [AUTH0, AUTHENTIK]


def test_candidate_issuers_dedupes_and_drops_empty():
    s = _settings(auth_provider="auth0", auth0_issuer=AUTH0, authentik_issuer="")
    assert s.candidate_issuers() == [AUTH0]


def test_profile_accepts_token_from_fallback_issuer(monkeypatch):
    """During a flip, a token minted by the *other* IdP still validates: the
    active issuer's userinfo rejects it (401) but the fallback accepts it."""
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

    def fake_get(url, headers):
        calls.append(url)
        # Reject at the active (authentik) issuer, accept at the auth0 fallback.
        if url.startswith(AUTHENTIK):
            return SimpleNamespace(status_code=401, text="unauthorized")
        return SimpleNamespace(
            status_code=200, json=lambda: {"email": "user@example.com"}
        )

    monkeypatch.setattr(auth_service.requests, "get", fake_get)

    profile = auth_service.get_profile_from_accesstoken("some-token")
    assert profile["email"] == "user@example.com"
    # Active issuer was tried first, then the fallback.
    assert calls[0].startswith(AUTHENTIK)
    assert calls[1].startswith(AUTH0)


def test_profile_rejected_when_no_issuer_accepts(monkeypatch):
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
        lambda url, headers: SimpleNamespace(status_code=401, text="nope"),
    )

    with pytest.raises(Exception):
        auth_service.get_profile_from_accesstoken("bad-token")
