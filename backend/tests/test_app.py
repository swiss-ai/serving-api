import pytest
from fastapi.testclient import TestClient
from testcontainers.postgres import PostgresContainer
from sqlmodel import SQLModel, create_engine


@pytest.fixture(scope="module")
def postgres():
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


@pytest.fixture(scope="module")
def client(postgres):
    import os

    os.environ["DATABASE_URL"] = postgres.get_connection_url()

    # Reset cached settings so it picks up the new DATABASE_URL
    from backend.config import get_settings

    get_settings.cache_clear()

    from backend.main import app

    # Create tables
    settings = get_settings()
    engine = create_engine(settings.database_url)
    SQLModel.metadata.create_all(engine)

    with TestClient(app) as c:
        yield c


def test_app_starts(client):
    """App boots and responds to requests."""
    response = client.get("/openapi.json")
    assert response.status_code == 200


def test_app_routes_registered(client):
    """All expected routes are registered."""
    response = client.get("/openapi.json")
    paths = response.json()["paths"]
    expected = [
        "/v1/chat/completions",
        "/v1/completions",
        "/v1/responses",
        "/v1/embeddings",
        "/v1/models",
        "/v1/models_detailed",
        "/v1/profile",
        "/v1/statistics",
        "/v1/metrics",
        "/v1/perf",
        "/v1/rerank",
        "/v1/score",
        "/v1/classify",
        "/v1/tokenize",
        "/v1/detokenize",
    ]
    for path in expected:
        assert path in paths, f"Missing route: {path}"


def test_models_endpoint_no_auth(client):
    """/v1/models should return 200 even when OpenTela is unreachable."""
    response = client.get("/v1/models")
    assert response.status_code == 200
    assert response.json()["object"] == "list"


def test_chat_completions_requires_auth(client):
    """/v1/chat/completions should reject unauthenticated requests."""
    response = client.post(
        "/v1/chat/completions", json={"model": "test", "messages": []}
    )
    assert response.status_code in (401, 403)


def test_responses_requires_auth(client):
    """/v1/responses should reject unauthenticated requests."""
    response = client.post("/v1/responses", json={"model": "test", "input": "hello"})
    assert response.status_code in (401, 403)


def test_rerank_requires_auth(client):
    """/v1/rerank should reject unauthenticated requests."""
    response = client.post(
        "/v1/rerank", json={"model": "test", "query": "q", "documents": []}
    )
    assert response.status_code in (401, 403)


def test_score_requires_auth(client):
    """/v1/score should reject unauthenticated requests."""
    response = client.post(
        "/v1/score", json={"model": "test", "text_1": "a", "text_2": "b"}
    )
    assert response.status_code in (401, 403)


def test_classify_requires_auth(client):
    """/v1/classify should reject unauthenticated requests."""
    response = client.post("/v1/classify", json={"model": "test", "input": "hello"})
    assert response.status_code in (401, 403)


def test_classify_forwards_to_pod_root(client, monkeypatch):
    """/v1/classify must forward to the vLLM pod root (/classify), not /v1/classify.
    vLLM serves classify without the /v1 prefix used by chat/completions/embeddings,
    so the upstream base must stop at ".../v1/service/llm/"."""
    import types

    import backend.routers.classify as classify_router
    from backend.middleware.auth import require_auth
    from backend.config import get_settings

    captured = {}

    async def fake_classify(*, endpoint, api_key, payload, model):
        captured["endpoint"] = endpoint
        return types.SimpleNamespace(data={"ok": True})

    monkeypatch.setattr(classify_router, "llm_proxy_classify", fake_classify)
    client.app.dependency_overrides[require_auth] = lambda: "test-token"
    try:
        response = client.post("/v1/classify", json={"model": "m", "input": "hello"})
    finally:
        client.app.dependency_overrides.pop(require_auth, None)

    assert response.status_code == 200
    base = get_settings().otela_head_addr
    assert captured["endpoint"] == base + "/v1/service/llm/"
    upstream = captured["endpoint"].rstrip("/") + "/classify"
    assert "/service/llm/v1/" not in upstream, upstream


def test_tokenize_requires_auth(client):
    """/v1/tokenize should reject unauthenticated requests."""
    response = client.post("/v1/tokenize", json={"model": "test", "prompt": "hello"})
    assert response.status_code in (401, 403)


def test_detokenize_requires_auth(client):
    """/v1/detokenize should reject unauthenticated requests."""
    response = client.post(
        "/v1/detokenize", json={"model": "test", "tokens": [1, 2, 3]}
    )
    assert response.status_code in (401, 403)


def test_statistics_no_auth(client):
    """/v1/statistics should work without auth."""
    response = client.get("/v1/statistics")
    assert response.status_code in (200, 500)


@pytest.mark.parametrize(
    "path,module_path,fn_name,payload",
    [
        (
            "/v1/score",
            "backend.routers.rerank",
            "llm_proxy_score",
            {"model": "m", "text_1": "a", "text_2": "b"},
        ),
        (
            "/v1/rerank",
            "backend.routers.rerank",
            "llm_proxy_rerank",
            {"model": "m", "query": "q", "documents": ["a"]},
        ),
        (
            "/v1/tokenize",
            "backend.routers.tokenization",
            "llm_proxy_tokenize",
            {"model": "m", "prompt": "hello"},
        ),
        (
            "/v1/detokenize",
            "backend.routers.tokenization",
            "llm_proxy_detokenize",
            {"model": "m", "tokens": [1, 2, 3]},
        ),
    ],
)
def test_pooling_endpoints_forward_to_pod_root(
    client, monkeypatch, path, module_path, fn_name, payload
):
    """Regression: pooling-family endpoints must forward to the vLLM pod root
    (e.g. /score), not /v1/score. vLLM serves these without the /v1 prefix used
    by chat/completions/embeddings, so the upstream base must stop at
    ".../v1/service/llm/". A stray "/v1/" here makes the pod 404."""
    import importlib
    import types

    from backend.middleware.auth import require_auth
    from backend.config import get_settings

    module = importlib.import_module(module_path)
    captured = {}

    async def fake_proxy(*, endpoint, api_key, payload, model):
        captured["endpoint"] = endpoint
        return types.SimpleNamespace(data={"ok": True})

    monkeypatch.setattr(module, fn_name, fake_proxy)
    client.app.dependency_overrides[require_auth] = lambda: "test-token"
    try:
        response = client.post(path, json=payload)
    finally:
        client.app.dependency_overrides.pop(require_auth, None)

    assert response.status_code == 200
    base = get_settings().otela_head_addr
    assert captured["endpoint"] == base + "/v1/service/llm/"
    # The composed upstream URL must hit the pod root, never a double-/v1 path.
    upstream = captured["endpoint"].rstrip("/") + path[len("/v1") :]
    assert "/service/llm/v1/" not in upstream, upstream
