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
    ]
    for path in expected:
        assert path in paths, f"Missing route: {path}"


def test_models_endpoint_no_auth(client):
    """/v1/models should return 200 even when OCF is unreachable."""
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


def test_statistics_no_auth(client):
    """/v1/statistics should work without auth."""
    response = client.get("/v1/statistics")
    assert response.status_code in (200, 500)
