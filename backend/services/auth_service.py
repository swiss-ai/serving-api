import json
import secrets
from pathlib import Path

import requests
from sqlmodel import Session, select
from backend.config import get_settings
from backend.models.entities import APIKey
from backend.redis_cache import get_token_cache


# LOCAL DEV ONLY. The frontend's dev session (api_key.astro) sends this token
# when running under `make run`; the backend honours it only when
# settings.dev_auth_bypass is explicitly enabled.
DEV_DUMMY_TOKEN = "dev-dummy-token"
DEV_EMAIL = "dev@localhost"


# Institutions whose members are auto-enabled (active budget on first sign-in).
# Canonical list — referenced from the public FAQ via its GitHub URL.
_SWISS_DOMAINS_FILE = Path(__file__).resolve().parent.parent / "swiss_domains.json"
with open(_SWISS_DOMAINS_FILE) as f:
    SWISS_DOMAINS = json.load(f)


def get_or_create_apikey(engine, owner_email: str) -> APIKey:
    with Session(engine) as session:
        api_key = session.exec(
            select(APIKey).where(APIKey.owner_email == owner_email)
        ).first()
        if api_key is None:
            key = f"sk-rc-{secrets.token_urlsafe(16)}"
            if owner_email == DEV_EMAIL or any(
                owner_email.lower().endswith(domain) for domain in SWISS_DOMAINS
            ):
                # Local dev account gets an active budget so the key is usable.
                budget = 1000
            else:
                budget = -1
            api_key = APIKey(key=key, owner_email=owner_email, budget=budget)
            session.add(api_key)
            session.commit()
            session.refresh(api_key)
        return api_key


def rotate_key(engine, key: str) -> APIKey:
    token_cache = get_token_cache()

    with Session(engine) as session:
        api_key = session.exec(select(APIKey).where(APIKey.key == key)).first()
        if api_key is None:
            raise ValueError("Invalid key")

        token_cache.remove_token(key)

        api_key.key = f"sk-rc-{secrets.token_urlsafe(16)}"
        session.add(api_key)
        session.commit()
        session.refresh(api_key)
        return api_key


def rotate_key_by_email(engine, owner_email: str) -> APIKey:
    token_cache = get_token_cache()

    with Session(engine) as session:
        api_key = session.exec(
            select(APIKey).where(APIKey.owner_email == owner_email)
        ).first()
        if api_key is None:
            raise ValueError("No API key for this user")

        token_cache.remove_token(api_key.key)

        api_key.key = f"sk-rc-{secrets.token_urlsafe(16)}"
        session.add(api_key)
        session.commit()
        session.refresh(api_key)
        return api_key


def verify_token(engine, token: str) -> bool:
    token_cache = get_token_cache()

    if token_cache.has_token(token):
        return True

    with Session(engine) as session:
        api_key = session.exec(select(APIKey).where(APIKey.key == token)).first()
        if api_key is None:
            return False
        if api_key.budget <= 0:
            return False

        token_cache.add_token(token, ttl=3600 * 7 * 30)
        return True


def dev_bypass_enabled() -> bool:
    """The dev auth bypass is honoured only when it is explicitly enabled AND
    the database is local. Deployed environments use a remote DB, so an
    accidental ``DEV_AUTH_BYPASS=true`` there stays inert — the flag alone is
    not enough to weaken auth in prod."""
    settings = get_settings()
    if not settings.dev_auth_bypass:
        return False
    return "localhost" in settings.database_url or "127.0.0.1" in settings.database_url


def get_profile_from_accesstoken(access_token: str):
    if dev_bypass_enabled() and access_token == DEV_DUMMY_TOKEN:
        # Local dev: skip Auth0 and return a fixed dev profile.
        return {"sub": "dev", "name": "Dev User", "email": DEV_EMAIL}
    res = requests.get(
        "https://researchcomputer.eu.auth0.com/userinfo",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {access_token}",
        },
    )
    if res.status_code != 200:
        raise Exception(f"Invalid access token: {res.status_code} {res.text}")
    return res.json()


def get_token_cache_stats() -> dict:
    token_cache = get_token_cache()
    return token_cache.get_cache_stats()


def clear_token_cache() -> bool:
    token_cache = get_token_cache()
    return token_cache.clear_cache()
