"""The local startup regression: keys must exist in migrated Postgres before login."""

import pytest
from sqlmodel import Session, delete, select

from backend.local_dev import (
    LOCAL_ACCOUNTS,
    main,
    require_local_database,
    seed_accounts,
)
from backend.models.entities import APIKey


def test_local_seeds_grant_admin_and_preserve_existing_keys(engine):
    emails = [account[0] for account in LOCAL_ACCOUNTS]
    with Session(engine) as session:
        session.exec(delete(APIKey).where(APIKey.owner_email.in_(emails)))
        session.commit()
    try:
        seed_accounts(engine)
        with Session(engine) as session:
            accounts = {
                row.owner_email: row
                for row in session.exec(
                    select(APIKey).where(APIKey.owner_email.in_(emails))
                )
            }
            assert len(accounts) == 3
            assert accounts["admin@localhost"].is_admin
            assert not accounts["user@localhost"].is_admin
            assert not accounts["chat@localhost"].is_admin
            assert accounts["chat@localhost"].key == "sk-rc-local-chat"
            admin = accounts["admin@localhost"]
            admin.key = "sk-rc-local-admin-rotated"
            admin.budget = 0
            admin.is_admin = False
            session.add(admin)
            session.commit()

        seed_accounts(engine)
        with Session(engine) as session:
            accounts = list(
                session.exec(select(APIKey).where(APIKey.owner_email.in_(emails)))
            )
            assert len(accounts) == 3
            admin = next(
                row for row in accounts if row.owner_email == "admin@localhost"
            )
            assert admin.key == "sk-rc-local-admin-rotated"
            assert admin.budget == 0
            assert admin.is_admin
    finally:
        with Session(engine) as session:
            session.exec(delete(APIKey).where(APIKey.owner_email.in_(emails)))
            session.commit()


def test_provider_waits_for_applied_blueprint(monkeypatch):
    from unittest.mock import Mock

    import requests

    from backend.local_dev import wait_for_provider

    issuer = "http://authentik.localhost:9000/application/o/serving-api-local/"
    not_ready = Mock()
    not_ready.raise_for_status.side_effect = requests.HTTPError("404 before blueprint")
    ready = Mock()
    ready.json.return_value = {
        "issuer": issuer,
        "token_endpoint": "http://authentik.localhost:9000/application/o/token/",
        "userinfo_endpoint": "http://authentik.localhost:9000/application/o/userinfo/",
    }
    get = Mock(side_effect=[not_ready, ready])
    monkeypatch.setattr("backend.local_dev.requests.get", get)
    monkeypatch.setattr("backend.local_dev.time.sleep", lambda _: None)

    wait_for_provider(issuer)
    assert get.call_count == 2


def test_provider_failure_aborts_bootstrap(monkeypatch):
    from unittest.mock import Mock

    import pytest
    import requests

    from backend.local_dev import wait_for_provider

    get = Mock(side_effect=requests.ConnectionError("IdP unavailable"))
    monkeypatch.setattr("backend.local_dev.requests.get", get)
    monkeypatch.setattr(
        "backend.local_dev.time.monotonic", Mock(side_effect=[0, 0, 200])
    )
    monkeypatch.setattr("backend.local_dev.time.sleep", lambda _: None)

    with pytest.raises(RuntimeError, match="provider was not ready"):
        wait_for_provider(
            "http://authentik.localhost:9000/application/o/serving-api-local/"
        )


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://serving:serving@postgres:5432/serving",
        "postgresql+psycopg2://serving:serving@localhost:5433/serving",
    ],
)
def test_local_database_is_allowed(url):
    require_local_database(url)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://app:secret@serving-api-prod-postgres:5432/serving",
        "postgresql://app:secret@db.example.org/serving",
        "postgresql:///serving",
    ],
)
def test_other_databases_are_refused(url):
    with pytest.raises(SystemExit):
        require_local_database(url)


def test_bootstrap_stops_before_touching_a_remote_database(monkeypatch):
    class Settings:
        database_url = "postgresql://app:secret@db.example.org/serving"

    def fail(*args):
        raise AssertionError("bootstrap went past the guard")

    monkeypatch.setattr("backend.local_dev.get_settings", lambda: Settings())
    monkeypatch.setattr("backend.local_dev.command.upgrade", fail)
    monkeypatch.setattr("backend.local_dev.create_engine", fail)

    with pytest.raises(SystemExit):
        main()
