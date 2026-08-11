"""Canonical model-id shape enforcement — unit tests for
``require_namespaced_model`` plus a router-level check that a bare id is
refused at the boundary, before any routing or proxying happens."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.middleware.auth import require_auth
from backend.middleware.model_id import require_namespaced_model
from backend.routers import completions


VALID_IDS = [
    "SwissAI-Research/swiss-ai/Apertus-70B-Instruct-2509",
    "CSCS-Inference/swiss-ai/Apertus-8B-Instruct-2509",
    "RCP-AIaaS/apertus-ai/Apertus-v1.5-8B-Prerelease-2607",
    "someuser/meta-llama/Llama-3.1-8B-Instruct",
]

INVALID_IDS = [
    "swiss-ai/Apertus-8B-Instruct-2509",  # historical bare upstream id
    "Apertus-8B-Instruct-2509",  # single segment
    "a/b/c/d",  # too many segments
    "SwissAI-Research//model",  # empty middle segment
    "/org/model",  # empty namespace
    "org/model/",  # empty model name
    "",
    "unknown",  # the routers' data.get("model", ...) default
    None,  # model key missing entirely
]


@pytest.mark.parametrize("model_id", VALID_IDS)
def test_valid_ids_pass_through(model_id):
    assert require_namespaced_model(model_id) == model_id


@pytest.mark.parametrize("model_id", INVALID_IDS)
def test_invalid_ids_raise_404(model_id):
    with pytest.raises(HTTPException) as exc_info:
        require_namespaced_model(model_id)
    assert exc_info.value.status_code == 404
    assert "<namespace>/<model_org>/<model_name>" in exc_info.value.detail


def _make_client() -> TestClient:
    app = FastAPI()
    app.include_router(completions.router)
    app.dependency_overrides[require_auth] = lambda: "test-token"
    return TestClient(app, raise_server_exceptions=False)


def test_chat_completion_rejects_bare_id_before_routing():
    """A bare id must be refused before _resolve_route runs — the old
    back-compat would otherwise have silently picked a provider."""
    client = _make_client()
    with patch.object(
        completions, "_resolve_route", new=AsyncMock(side_effect=AssertionError)
    ) as route:
        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "swiss-ai/Apertus-8B-Instruct-2509",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
    assert resp.status_code == 404
    assert "<namespace>/<model_org>/<model_name>" in resp.json()["detail"]
    route.assert_not_awaited()
