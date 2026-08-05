from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from testcontainers.postgres import PostgresContainer
from sqlmodel import SQLModel, Session, create_engine


@pytest.fixture(scope="module")
def postgres():
    with PostgresContainer("postgres:17-alpine") as pg:
        yield pg


@pytest.fixture(scope="module")
def client(postgres):
    import os

    os.environ["DATABASE_URL"] = postgres.get_connection_url()

    from backend.config import get_settings

    get_settings.cache_clear()

    from backend.main import app

    settings = get_settings()
    engine = create_engine(settings.database_url)
    SQLModel.metadata.create_all(engine)

    with TestClient(app) as c:
        # main.py's lifespan builds the engine from the module-level settings
        # captured at FIRST import — under a full suite run that is another
        # test module's (already torn down) postgres container. Point the app
        # at this module's container explicitly.
        c.app.state.engine = engine
        yield c


@pytest.fixture()
def engine(client):
    return client.app.state.engine


@pytest.fixture(autouse=True)
def clean_rules(engine):
    from backend.models.entities import UserMonitoringRule
    from backend.services.monitoring_service import _effective_level_cache

    yield
    with Session(engine) as session:
        for r in session.query(UserMonitoringRule).all():
            session.delete(r)
        session.commit()
    _effective_level_cache.clear()


def _admin_override(client):
    from backend.routers.admin_monitoring import require_admin

    client.app.dependency_overrides[require_admin] = lambda: "admin@test.ch"
    return require_admin


# ---------- rule semantics ----------


def test_upsert_rejects_bad_values(engine):
    from backend.services.monitoring_service import upsert_rule

    with pytest.raises(ValueError):
        upsert_rule(engine, "a@b.ch", "verbose", "admin", "1h", "adm")
    with pytest.raises(ValueError):
        upsert_rule(engine, "a@b.ch", "full", "admin", "forever", "adm")
    with pytest.raises(ValueError):
        upsert_rule(engine, "a@b.ch", "full", "robot", "1h", "adm")


def test_effective_level_is_max_of_active_rules(engine):
    from backend.services.monitoring_service import (
        get_effective_level,
        upsert_rule,
        _effective_level_cache,
    )

    upsert_rule(engine, "u@ethz.ch", "metadata", "admin", "1d", "adm")
    assert get_effective_level(engine, "u@ethz.ch") == "metadata"

    upsert_rule(engine, "u@ethz.ch", "full", "self", "1h", "u@ethz.ch")
    assert get_effective_level(engine, "u@ethz.ch") == "full"

    _effective_level_cache.clear()
    assert get_effective_level(engine, "nobody@ethz.ch") is None


def test_expired_rules_are_inert(engine):
    from backend.models.entities import UserMonitoringRule
    from backend.services.monitoring_service import (
        get_effective_level,
        _effective_level_cache,
    )

    with Session(engine) as session:
        session.add(
            UserMonitoringRule(
                owner_email="old@ethz.ch",
                level="full",
                source="admin",
                expires_at=datetime.now() - timedelta(hours=1),
                created_by="adm",
            )
        )
        session.commit()
    _effective_level_cache.clear()
    assert get_effective_level(engine, "old@ethz.ch") is None


def test_upsert_renews_instead_of_duplicating(engine):
    from backend.models.entities import UserMonitoringRule
    from backend.services.monitoring_service import upsert_rule

    upsert_rule(engine, "r@ethz.ch", "metadata", "admin", "1h", "adm")
    upsert_rule(engine, "r@ethz.ch", "full", "admin", "7d", "adm2")
    with Session(engine) as session:
        rules = session.query(UserMonitoringRule).all()
    assert len(rules) == 1
    assert rules[0].level == "full"
    assert rules[0].created_by == "adm2"


def test_default_policy_metadata_for_everyone(engine):
    from backend.services.monitoring_service import (
        resolve_trace_level,
        upsert_rule,
        _effective_level_cache,
    )

    # No rule: metadata by default, unconditionally — there is no opt-out.
    assert resolve_trace_level(engine, "any@ethz.ch") == ("metadata", True)

    # An explicit rule overrides the default.
    upsert_rule(engine, "watched@ethz.ch", "full", "admin", "1h", "adm")
    assert resolve_trace_level(engine, "watched@ethz.ch") == ("full", False)
    _effective_level_cache.clear()


def test_prepare_stream_trace_levels(engine):
    from backend.services.langfuse_service import prepare_stream_trace
    from backend.services.monitoring_service import (
        upsert_rule,
        _effective_level_cache,
        _owner_email_cache,
    )

    _make_key(engine, "sk-rc-stream-user", "stream@ethz.ch")
    _owner_email_cache.clear()

    # Default: metadata trace ctx, no prompt captured.
    ctx = prepare_stream_trace(
        engine, "sk-rc-stream-user", "m", {"messages": [{"role": "user"}]}
    )
    assert ctx["level"] == "metadata" and ctx["is_default"] is True
    assert "input" not in ctx

    # Full rule: prompt captured into the ctx.
    upsert_rule(engine, "stream@ethz.ch", "full", "admin", "1h", "adm")
    ctx = prepare_stream_trace(
        engine, "sk-rc-stream-user", "m", {"messages": [{"role": "user"}]}
    )
    assert ctx["level"] == "full" and ctx["input"] == [{"role": "user"}]

    # Unknown key: no trace.
    assert prepare_stream_trace(engine, "sk-rc-ghost", "m", {}) is None
    _effective_level_cache.clear()


# ---------- admin API ----------


def _make_key(engine, key, email, admin=False):
    from backend.models.entities import APIKey

    with Session(engine) as session:
        session.add(APIKey(key=key, owner_email=email, budget=1000, is_admin=admin))
        session.commit()


def test_admin_gate_by_is_admin_flag(client, engine):
    from backend.services.monitoring_service import _owner_email_cache

    _make_key(engine, "sk-rc-test-admin", "boss@ethz.ch", admin=True)
    _make_key(engine, "sk-rc-test-pleb", "user@ethz.ch", admin=False)
    _owner_email_cache.clear()

    ok = client.get(
        "/v1/admin/monitoring/users",
        headers={"Authorization": "Bearer sk-rc-test-admin"},
    )
    assert ok.status_code == 200

    denied = client.get(
        "/v1/admin/monitoring/users",
        headers={"Authorization": "Bearer sk-rc-test-pleb"},
    )
    assert denied.status_code == 403

    # Not an API key and not a valid IdP token either.
    invalid = client.get(
        "/v1/admin/monitoring/users",
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert invalid.status_code == 401


def test_admin_crud_roundtrip(client):
    _admin_override(client)
    try:
        create = client.post(
            "/v1/admin/monitoring/users",
            json={
                "owner_email": "watched@ethz.ch",
                "level": "full",
                "ttl": "6h",
                "note": "debug ticket #42",
            },
        )
        assert create.status_code == 200
        assert create.json()["source"] == "admin"

        listing = client.get("/v1/admin/monitoring/users").json()
        emails = [r["owner_email"] for r in listing["rules"]]
        assert "watched@ethz.ch" in emails

        bad = client.post(
            "/v1/admin/monitoring/users",
            json={"owner_email": "x@ethz.ch", "level": "full", "ttl": "forever"},
        )
        assert bad.status_code == 422

        deleted = client.delete("/v1/admin/monitoring/users/watched@ethz.ch")
        assert deleted.status_code == 200
        assert deleted.json()["removed"] == 1

        again = client.delete("/v1/admin/monitoring/users/watched@ethz.ch")
        assert again.status_code == 404
    finally:
        client.app.dependency_overrides.clear()


# ---------- self-serve API ----------


def test_profile_monitoring_roundtrip(client, monkeypatch):
    import backend.routers.profile as profile_router

    monkeypatch.setattr(
        profile_router,
        "get_profile_from_accesstoken",
        lambda token: {"email": "me@ethz.ch"},
    )
    headers = {"Authorization": "Bearer idp-token"}

    empty = client.get("/v1/profile/monitoring", headers=headers).json()
    assert empty["self_rule"] is None
    assert empty["effective_level"] is None

    put = client.put(
        "/v1/profile/monitoring",
        json={"level": "full", "ttl": "1d"},
        headers=headers,
    )
    assert put.status_code == 200
    assert put.json()["source"] == "self"

    state = client.get("/v1/profile/monitoring", headers=headers).json()
    assert state["self_rule"]["level"] == "full"
    assert state["effective_level"] == "full"

    off = client.delete("/v1/profile/monitoring", headers=headers)
    assert off.json()["removed"] == 1
