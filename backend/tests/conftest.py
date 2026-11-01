"""Shared fixtures: one Postgres container per test session, migrated with
alembic exactly like prod, and one TestClient bound to it.

The schema comes from `alembic upgrade head`, not SQLModel.metadata.create_all,
so tests run against the same column types prod has (e.g. timestamp WITHOUT
time zone). create_all derives the schema from the models, which hides any
drift between the models and the migrations.
"""

import os
from pathlib import Path

import pytest
from sqlmodel import create_engine

REPO_ROOT = Path(__file__).resolve().parents[2]


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "e2e: real sign-up flow against Authentik in testcontainers "
        "(slow, needs Docker; run with `make test-e2e`)",
    )


def run_migrations(database_url: str) -> None:
    from alembic import command
    from alembic.config import Config

    from backend.config import get_settings

    # alembic/env.py reads the URL from get_settings(), which is lru_cached.
    os.environ["DATABASE_URL"] = database_url
    get_settings.cache_clear()
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "backend" / "alembic"))
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session")
def database_url():
    from testcontainers.postgres import PostgresContainer

    # Same major version as prod (serving-api-prod-postgres runs postgres:17).
    with PostgresContainer("postgres:17-alpine") as pg:
        url = pg.get_connection_url()
        run_migrations(url)
        yield url


@pytest.fixture(scope="session")
def client(database_url):
    from fastapi.testclient import TestClient

    os.environ["DATABASE_URL"] = database_url
    from backend.config import get_settings

    get_settings.cache_clear()

    from backend.main import app

    engine = create_engine(database_url, pool_pre_ping=True)
    with TestClient(app) as c:
        # main.py's lifespan builds its engine from settings captured at first
        # import, which may predate DATABASE_URL being set. Pin it here.
        c.app.state.engine = engine
        yield c
    engine.dispose()


@pytest.fixture()
def engine(client):
    return client.app.state.engine
