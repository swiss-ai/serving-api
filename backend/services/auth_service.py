import secrets
import requests
from sqlmodel import Session, select
from backend.models.entities import APIKey
from backend.redis_cache import get_token_cache


SWISS_DOMAINS = [
    "ethz.ch",
    "cscs.ch",
    "unibas.ch",
    "unibe.ch",
    "uzh.ch",
    "epfl.ch",
    "unil.ch",
    "unige.ch",
    "hevs.ch",
]


def get_or_create_apikey(engine, owner_email: str) -> APIKey:
    with Session(engine) as session:
        api_key = session.exec(
            select(APIKey).where(APIKey.owner_email == owner_email)
        ).first()
        if api_key is None:
            key = f"sk-rc-{secrets.token_urlsafe(16)}"
            if any(
                owner_email.lower().endswith(domain) for domain in SWISS_DOMAINS
            ):
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


def get_profile_from_accesstoken(access_token: str):
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
