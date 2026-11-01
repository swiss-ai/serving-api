"""A new user's first sign-in, end to end over HTTP against migrated Postgres.

Only the IdP userinfo call is faked; everything from the /v1/profile router
down (key creation, the insert, verify_token on the inference path) is real.
The Authentik-backed version of this flow is in test_e2e_authentik.py.
"""

import uuid

import pytest


@pytest.fixture()
def signed_in_as(client, monkeypatch):
    """Make every IdP access token resolve to the given email."""
    from backend.routers import profile as profile_router

    def _as(email):
        monkeypatch.setattr(
            profile_router,
            "get_profile_from_accesstoken",
            lambda access_token: {"email": email, "sub": "test"},
        )
        return {"Authorization": "Bearer idp-access-token"}

    return _as


def _api_key_status(client, key):
    return client.get("/v1/mcp", headers={"Authorization": f"Bearer {key}"}).status_code


def _new_email():
    return f"signup-{uuid.uuid4().hex[:8]}@example.org"


def test_first_signin_over_http_issues_a_working_key(client, signed_in_as):
    headers = signed_in_as(_new_email())

    first = client.get("/v1/profile", headers=headers)
    assert first.status_code == 200, first.text
    key = first.json()["api_key"]
    assert key.startswith("sk-rc-")
    assert first.json()["budget"] > 0

    # A second sign-in returns the same key rather than minting another.
    again = client.get("/v1/profile", headers=headers)
    assert again.status_code == 200
    assert again.json()["api_key"] == key

    # The key passes the same require_auth check the inference routes use.
    # /v1/mcp is behind it and answers locally, so no upstream is involved.
    assert _api_key_status(client, key) == 200
    # Control: a made-up key is rejected.
    assert _api_key_status(client, "sk-rc-not-a-real-key") == 401


def test_rotated_key_replaces_the_old_one(client, signed_in_as):
    headers = signed_in_as(_new_email())
    old = client.get("/v1/profile", headers=headers).json()["api_key"]

    rotated = client.post("/v1/profile/rotate", headers=headers)
    assert rotated.status_code == 200
    new = rotated.json()["api_key"]
    assert new != old

    assert _api_key_status(client, old) == 401
    assert _api_key_status(client, new) == 200


def test_profile_rejects_bad_idp_token_with_401(client, monkeypatch):
    from backend.routers import profile as profile_router

    def reject(access_token):
        raise Exception("Invalid access token: 401")

    monkeypatch.setattr(profile_router, "get_profile_from_accesstoken", reject)
    response = client.get("/v1/profile", headers={"Authorization": "Bearer bad"})
    assert response.status_code == 401


def test_profile_reports_key_failures_as_500_not_401(client, signed_in_as, monkeypatch):
    """Regression for the sqlmodel 0.0.46 outage: the key insert failed but
    users got 401 "Invalid access token", which looked like expired logins."""
    from backend.routers import profile as profile_router

    def broken(engine, email):
        raise ValueError("insert rejected")

    monkeypatch.setattr(profile_router, "get_or_create_apikey", broken)
    response = client.get("/v1/profile", headers=signed_in_as(_new_email()))
    assert response.status_code == 500
