import os
import secrets
import requests
from datetime import datetime
from sqlmodel import SQLModel, Field, Session, create_engine, select
from .redis_cache import get_token_cache


class APIKey(SQLModel, table=True):
    key: str = Field(primary_key=True)
    budget: int = Field(default=1000)
    created_at: datetime = Field(default=datetime.now())
    updated_at: datetime = Field(default=datetime.now())
    owner_email: str = Field(default="")


def get_or_create_apikey(engine, owner_email: str) -> APIKey:
    with Session(engine) as session:
        api_key = session.exec(
            select(APIKey).where(APIKey.owner_email == owner_email)
        ).first()
        if api_key is None:
            key = f"sk-rc-{secrets.token_urlsafe(16)}"
            if any(
                [
                    owner_email.lower().endswith(x)
                    for x in [
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
                ]
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

        # Remove old key from cache
        token_cache.remove_token(key)

        # Generate new key
        api_key.key = f"sk-rc-{secrets.token_urlsafe(16)}"
        session.add(api_key)
        session.commit()
        session.refresh(api_key)
        return api_key


def verify_token(engine, token: str) -> bool:
    token_cache = get_token_cache()

    # Check if token is cached in Redis
    if token_cache.has_token(token):
        return True

    # If not in cache, check database
    with Session(engine) as session:
        api_key = session.exec(select(APIKey).where(APIKey.key == token)).first()
        if api_key is None:
            return False
        if api_key.budget <= 0:
            return False

        # Add valid token to cache (cache for 1 month)
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
    user = res.json()
    return user


def get_token_cache_stats() -> dict:
    """Get statistics about the token cache"""
    token_cache = get_token_cache()
    return token_cache.get_cache_stats()


def clear_token_cache() -> bool:
    """Clear all tokens from the cache (useful for maintenance)"""
    token_cache = get_token_cache()
    return token_cache.clear_cache()


if __name__ == "__main__":
    pg_host = os.environ.get("PG_HOST", "localhost")
    engine = create_engine(pg_host, echo=True)
    SQLModel.metadata.create_all(engine)
