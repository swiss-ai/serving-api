"""One-shot bootstrap for the Docker local development stack."""

import time

import requests
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url
from sqlmodel import Session, create_engine, select

from backend.config import get_settings
from backend.models.entities import APIKey


LOCAL_ACCOUNTS = (
    ("admin@localhost", "sk-rc-local-admin", True),
    ("user@localhost", "sk-rc-local-user", False),
    ("chat@localhost", "sk-rc-local-chat", False),
)

# The Compose service plus the host's view of it. Nothing else may be seeded.
LOCAL_DATABASE_HOSTS = ("postgres", "localhost", "127.0.0.1")


def require_local_database(database_url: str) -> None:
    """This module ships in the deployed image and seeds an admin with a key
    that's public in this file. Refuse any database but the local one."""
    host = make_url(database_url).host
    if host not in LOCAL_DATABASE_HOSTS:
        raise SystemExit(f"Refusing to bootstrap non-local database host: {host}")


def seed_accounts(engine) -> None:
    """Create local keys once; preserve rotations and budgets on later starts."""
    with Session(engine) as session:
        for email, key, is_admin in LOCAL_ACCOUNTS:
            account = session.exec(
                select(APIKey).where(APIKey.owner_email == email)
            ).first()
            if account is None:
                account = APIKey(key=key, owner_email=email, budget=1000)
            if is_admin:
                account.is_admin = True
            session.add(account)
        session.commit()


def wait_for_provider(issuer: str, timeout: float = 120) -> None:
    """Container health alone does not mean the local blueprint has applied."""
    deadline = time.monotonic() + timeout
    discovery = issuer.rstrip("/") + "/.well-known/openid-configuration"
    last_error = None
    while time.monotonic() < deadline:
        try:
            response = requests.get(discovery, timeout=(3, 5))
            response.raise_for_status()
            document = response.json()
            if document.get("issuer") != issuer:
                raise ValueError("OIDC discovery returned a different issuer")
            if not document.get("token_endpoint") or not document.get(
                "userinfo_endpoint"
            ):
                raise ValueError("OIDC discovery is missing required endpoints")
            return
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
        time.sleep(2)
    raise RuntimeError(
        f"Local Authentik provider was not ready: {discovery}"
    ) from last_error


def main() -> None:
    settings = get_settings()
    require_local_database(settings.database_url)
    # A failure aborts bootstrap and keeps the app from starting.
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_engine(settings.database_url)
    try:
        seed_accounts(engine)
    finally:
        engine.dispose()
    wait_for_provider(settings.active_issuer())
    print("Local database migrated, accounts seeded, and Authentik provider ready.")


if __name__ == "__main__":
    main()
