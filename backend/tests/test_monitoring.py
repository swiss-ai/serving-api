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
    # No rule still means the default metadata tier applies.
    assert empty["effective_level"] == "metadata"
    assert empty["default"] is True

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
    assert state["default"] is False

    off = client.delete("/v1/profile/monitoring", headers=headers)
    assert off.json()["removed"] == 1


# ---------- usage metrics helpers ----------


def test_normalize_usage_openai_keys():
    from backend.services.langfuse_service import _normalize_usage

    assert _normalize_usage({"prompt_tokens": 10, "completion_tokens": 5}) == {
        "promptTokens": 10,
        "completionTokens": 5,
        "totalTokens": 15,
    }
    assert _normalize_usage({"total_tokens": 7}) == {"totalTokens": 7}
    assert _normalize_usage({}) is None
    assert _normalize_usage(None) is None


def test_aggregate_user_activity_orders_and_sums():
    from backend.services.langfuse_service import aggregate_user_activity

    traces = [
        {
            "userId": "a@x.ch",
            "metadata": {"usage": {"total_tokens": 10}},
            "timestamp": "2026-08-05T10:00:00Z",
        },
        {
            "userId": "b@x.ch",
            "metadata": {"usage": {"completion_tokens": 3}},
            "timestamp": "2026-08-05T11:00:00Z",
        },
        {"userId": "a@x.ch", "metadata": {}, "timestamp": "2026-08-05T12:00:00Z"},
        {"userId": None, "metadata": {}},
    ]
    out = aggregate_user_activity(traces)
    assert [u["user"] for u in out] == ["a@x.ch", "b@x.ch"]
    assert out[0]["requests"] == 2 and out[0]["total_tokens"] == 10
    assert out[0]["last_active"] == "2026-08-05T12:00:00Z"
    assert out[1]["total_tokens"] == 3


# ---------- perf benchmarks (postgres-backed) ----------


def test_merged_averages_math():
    from backend.services.metrics_service import merged_averages

    count, avgs = merged_averages(
        2,
        {"avg_ttft": 1.0, "avg_latency": 10.0, "avg_throughput": 100.0},
        {
            "count": 2,
            "total_ttft": 6.0,
            "total_latency": 20.0,
            "total_throughput": 100.0,
        },
    )
    assert count == 4
    assert avgs == {"avg_ttft": 2.0, "avg_latency": 10.0, "avg_throughput": 75.0}


def test_sync_and_fetch_benchmarks(engine):
    from backend.services.metrics_service import fetch_benchmarks, sync_benchmark

    stats = {
        "count": 5,
        "total_ttft": 5.0,
        "total_latency": 50.0,
        "total_throughput": 500.0,
    }
    sync_benchmark(engine, "model-x", "4x GH200", stats)
    sync_benchmark(engine, "model-x", "4x GH200", stats)

    rows = fetch_benchmarks(engine, "model-x")
    assert len(rows) == 1
    r = rows[0]
    assert r["count"] == 10
    assert r["avg_ttft"] == 1.0
    assert r["avg_latency"] == 10.0
    assert r["avg_throughput"] == 100.0
    assert fetch_benchmarks(engine, "other-model") == []


# ---------- model listing curation ----------


def _peer(model_id, version="sai-v0.0.6"):
    return {"id": model_id, "otela_version": version}


def test_listing_keeps_platform_and_recent_user_launches(monkeypatch):
    from backend.config import get_settings
    from backend.services.model_service import platform_namespaced

    get_settings.cache_clear()
    monkeypatch.setenv("ENFORCE_MODEL_NAMESPACE", "true")
    monkeypatch.setenv("MIN_USER_OTELA_VERSION", "sai-v0.0.6")
    peers = [
        _peer(
            "SwissAI-Research/swiss-ai/Apertus-v1.5-8B", ""
        ),  # ours: version irrelevant
        _peer("rsmith/swiss-ai/Apertus-v1.5-8B"),  # user, current version
        _peer("rsmith/Qwen/Qwen3-32B", "sai-v0.0.10"),  # user, newer (numeric compare)
        _peer("olduser/meta-llama/Llama-3.3-70B", "sai-v0.0.5"),  # user, too old
        _peer("nover/meta-llama/Llama-3.3-70B", ""),  # user, no version
        _peer("swiss-ai/Apertus-v1.5-8B"),  # two segments
        _peer("judge-qwen36-35b"),  # bare name
        _peer("/capstor/store/cscs/swissai/models"),  # checkpoint path
        _peer("SwissAI-Research/a/b/c"),  # four segments
        _peer("SwissAI-Research//m"),  # empty segment
    ]
    kept = [m["id"] for m in platform_namespaced(peers)]
    assert kept == [
        "SwissAI-Research/swiss-ai/Apertus-v1.5-8B",
        "rsmith/swiss-ai/Apertus-v1.5-8B",
        "rsmith/Qwen/Qwen3-32B",
    ]
    get_settings.cache_clear()


def test_version_compare_is_numeric_not_lexical():
    from backend.services.model_service import _at_least

    assert _at_least("sai-v0.0.10", "sai-v0.0.6")  # lexically "10" < "6"
    assert _at_least("sai-v0.1.0", "sai-v0.0.6")
    assert _at_least("sai-v0.0.6", "sai-v0.0.6")
    assert not _at_least("sai-v0.0.5", "sai-v0.0.6")
    assert not _at_least("", "sai-v0.0.6")
    assert _at_least("", "")  # no floor configured -> everything passes


def test_listing_filter_can_be_disabled(monkeypatch):
    from backend.config import get_settings
    from backend.services.model_service import platform_namespaced

    get_settings.cache_clear()
    monkeypatch.setenv("ENFORCE_MODEL_NAMESPACE", "false")
    peers = [
        _peer("SwissAI-Research/o/m"),
        _peer("bare"),
        _peer("old/o/m", "sai-v0.0.1"),
    ]
    assert len(platform_namespaced(peers)) == 3
    get_settings.cache_clear()


# ---------- usage accounting ----------


@pytest.fixture(autouse=True)
def clean_usage(engine):
    from backend.models.entities import UsageDaily
    from backend.services import usage_service

    yield
    with Session(engine) as session:
        for r in session.query(UsageDaily).all():
            session.delete(r)
        session.commit()
    usage_service.flush(engine)  # drain anything buffered by a test
    with Session(engine) as session:
        for r in session.query(UsageDaily).all():
            session.delete(r)
        session.commit()


def test_usage_accumulates_and_upserts(engine):
    from backend.services import usage_service

    for _ in range(3):
        usage_service.record_usage("a@ethz.ch", "SwissAI-Research/o/m", 100, 20)
    usage_service.record_usage("b@epfl.ch", "CSCS-Inference/o/m", 7, 3)
    assert usage_service.flush(engine) == 2

    # A second window for the same key must add, not replace.
    usage_service.record_usage("a@ethz.ch", "SwissAI-Research/o/m", 50, 5)
    usage_service.flush(engine)

    rows = {r["user"]: r for r in usage_service.usage_by_user(engine, days=1)}
    assert rows["a@ethz.ch"]["requests"] == 4
    assert rows["a@ethz.ch"]["prompt_tokens"] == 350
    assert rows["a@ethz.ch"]["completion_tokens"] == 65
    assert rows["a@ethz.ch"]["total_tokens"] == 415
    assert rows["b@epfl.ch"]["requests"] == 1


def test_usage_by_model_and_per_user_views(engine):
    from backend.services import usage_service

    usage_service.record_usage("a@ethz.ch", "model-x", 10, 1)
    usage_service.record_usage("b@epfl.ch", "model-x", 20, 2)
    usage_service.record_usage("a@ethz.ch", "model-y", 5, 5)
    usage_service.flush(engine)

    by_model = {r["model"]: r for r in usage_service.usage_by_model(engine, days=1)}
    assert by_model["model-x"]["requests"] == 2  # both users
    assert by_model["model-x"]["prompt_tokens"] == 30

    mine = usage_service.usage_for_user(engine, "a@ethz.ch", days=1)
    assert {r["model"] for r in mine} == {"model-x", "model-y"}  # only my rows
    assert sum(r["requests"] for r in mine) == 2


def test_usage_ranked_by_requests(engine):
    from backend.services import usage_service

    usage_service.record_usage("quiet@ethz.ch", "m", 1, 1)
    for _ in range(5):
        usage_service.record_usage("busy@ethz.ch", "m", 1, 1)
    usage_service.flush(engine)
    assert [r["user"] for r in usage_service.usage_by_user(engine, days=1)] == [
        "busy@ethz.ch",
        "quiet@ethz.ch",
    ]


def test_usage_ignores_incomplete_records(engine):
    from backend.services import usage_service

    usage_service.record_usage("", "m", 1, 1)  # no user
    usage_service.record_usage("a@ethz.ch", "", 1, 1)  # no model
    assert usage_service.flush(engine) == 0


# ---------- admin all-models view ----------


def test_admin_models_lists_everything_with_hidden_reasons(client):
    """The admin view returns every model from every source, aggregated by
    id, with hidden_reason explaining what the public filter would drop —
    including the real-world failure mode of a username-suffixed 2-segment
    id from a current OpenTela launch."""
    from unittest.mock import AsyncMock, patch

    require_admin = _admin_override(client)
    peers = [
        {
            "id": "SwissAI-Research/swiss-ai/Apertus-v1.5-8B",
            "launched_by": "k8s",
            "otela_version": "",
            "status": "ready",
            "device": "GH200",
        },
        {
            "id": "SwissAI-Research/swiss-ai/Apertus-v1.5-8B",
            "launched_by": "k8s",
            "otela_version": "",
            "status": "ready",
            "device": "GH200",
        },
        {
            "id": "MiniMaxAI/MiniMax-M2.5-bdoan",
            "launched_by": "bdoan",
            "otela_version": "sai-v0.0.6",
            "status": "ready",
            "device": "GH200",
        },
        {
            "id": "user1/some-org/some-model",
            "launched_by": "user1",
            "otela_version": "sai-v0.0.5",
            "status": "ready",
            "device": "GH200",
        },
    ]
    inventory = [
        {
            "id": "RCP-AIaaS/swiss-ai/Apertus-v1.5-8B",
            "source": "rcp",
            "device": "EPFL RCP",
            "hidden_reason": None,
        },
        {
            "id": "RCP-AIaaS/swiss-ai/Apertus-v1.5-8B-bfloat16",
            "source": "rcp",
            "device": "EPFL RCP",
            "hidden_reason": "alias/quant suffix hidden by curation",
        },
    ]
    try:
        with (
            patch(
                "backend.services.model_service.get_all_models",
                return_value=peers,
            ),
            patch(
                "backend.services.passthrough_service.admin_inventory",
                new=AsyncMock(return_value=inventory),
            ),
        ):
            res = client.get("/v1/admin/models")
    finally:
        client.app.dependency_overrides.pop(require_admin, None)
    assert res.status_code == 200
    body = res.json()
    assert body["count"] == 5
    by_id = {m["id"]: m for m in body["models"]}
    # Ours: listed, and the two peers aggregate into one row.
    ours = by_id["SwissAI-Research/swiss-ai/Apertus-v1.5-8B"]
    assert ours["hidden_reason"] is None and ours["peers"] == 2
    # Username-suffixed 2-segment id: hidden with the naming reason.
    assert "3 segments" in by_id["MiniMaxAI/MiniMax-M2.5-bdoan"]["hidden_reason"]
    # Well-formed user launch on an old OpenTela: hidden with the version reason.
    assert "below" in by_id["user1/some-org/some-model"]["hidden_reason"]
    # Passthrough rows pass through their curation verdicts.
    assert by_id["RCP-AIaaS/swiss-ai/Apertus-v1.5-8B"]["hidden_reason"] is None
    assert (
        by_id["RCP-AIaaS/swiss-ai/Apertus-v1.5-8B-bfloat16"]["hidden_reason"]
        is not None
    )
    # Hidden entries sort first — they are the page's point.
    assert body["models"][0]["hidden_reason"] is not None
