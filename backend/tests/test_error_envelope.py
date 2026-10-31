import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

# Import backend.main lazily inside the tests — a module-level import runs
# during collection and freezes settings before test_app.py sets DATABASE_URL.


def _make_client() -> TestClient:
    from backend.main import (
        http_exception_handler,
        unhandled_exception_handler,
        validation_exception_handler,
    )

    app = FastAPI()
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)

    @app.get("/boom")
    def boom():
        raise HTTPException(status_code=401, detail="Invalid access token")

    class Body(BaseModel):
        x: int

    @app.post("/validate")
    def validate(body: Body):
        return {"ok": True}

    @app.get("/crash")
    def crash():
        raise ValueError("secret internal detail")

    return TestClient(app, raise_server_exceptions=False)


def test_http_error_uses_openai_envelope():
    client = _make_client()
    resp = client.get("/boom")
    assert resp.status_code == 401
    payload = resp.json()
    assert "detail" not in payload
    err = payload["error"]
    assert err["message"] == "Invalid access token"
    assert err["type"] == "authentication_error"
    assert err["param"] is None
    assert "code" in err


def test_validation_error_uses_openai_envelope():
    client = _make_client()
    resp = client.post("/validate", json={"x": "not-an-int"})
    assert resp.status_code == 422
    payload = resp.json()
    assert "detail" not in payload
    err = payload["error"]
    assert isinstance(err["message"], str) and err["message"]
    assert err["type"] == "invalid_request_error"
    assert err["param"] == "x"
    assert err["code"] == "invalid_request"


def test_unhandled_error_uses_openai_envelope_without_leaking_details():
    client = _make_client()
    resp = client.get("/crash")
    assert resp.status_code == 500
    payload = resp.json()
    assert "detail" not in payload
    err = payload["error"]
    assert err["type"] == "api_error"
    assert "secret internal detail" not in resp.text
    assert err["message"] == "Internal server error"


def test_error_type_mapping():
    from backend.main import _openai_error_type

    assert _openai_error_type(400) == "invalid_request_error"
    assert _openai_error_type(401) == "authentication_error"
    assert _openai_error_type(403) == "permission_error"
    assert _openai_error_type(404) == "invalid_request_error"
    assert _openai_error_type(422) == "invalid_request_error"
    assert _openai_error_type(429) == "rate_limit_error"
    assert _openai_error_type(500) == "api_error"
    assert _openai_error_type(503) == "api_error"


def _upstream_client(body: str, status: int = 404) -> TestClient:
    from backend.main import backend_http_error_handler
    from backend.models.protocols import BackendHTTPError

    app = FastAPI()
    app.add_exception_handler(BackendHTTPError, backend_http_error_handler)

    @app.post("/v1/rerank")
    def rerank():
        raise BackendHTTPError(status, body, model="org/m", provider="EPFL RCP")

    return TestClient(app)


def test_upstream_html_error_is_wrapped_without_the_page():
    resp = _upstream_client("<html><body><h1>404 Not Found</h1></body></html>").post(
        "/v1/rerank"
    )
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/json")
    err = resp.json()["error"]
    assert err["type"] == "upstream_error"
    assert err["message"] == (
        "upstream EPFL RCP returned 404 for model org/m on /v1/rerank"
    )


def test_upstream_plain_text_error_keeps_truncated_reason():
    resp = _upstream_client("No provider found" + "x" * 1000, status=503).post(
        "/v1/rerank"
    )
    assert resp.status_code == 503
    message = resp.json()["error"]["message"]
    assert message.startswith(
        "upstream EPFL RCP returned 503 for model org/m on /v1/rerank: "
        "No provider found"
    )
    assert len(message) < 600


@pytest.mark.parametrize("body", ["", None])
def test_upstream_empty_error_is_wrapped(body):
    resp = _upstream_client(body, status=502).post("/v1/rerank")
    assert resp.status_code == 502
    assert resp.json()["error"]["type"] == "upstream_error"


def test_upstream_json_error_passes_through_unchanged():
    body = '{"error":{"message":"bad model","type":"invalid_request_error"}}'
    resp = _upstream_client(body, status=400).post("/v1/rerank")
    assert resp.status_code == 400
    assert resp.text == body


def test_proxy_handler_attaches_model_and_provider():
    from backend.models.protocols import BackendHTTPError
    from backend.services import llm_service

    async def call():
        await llm_service._shared_proxy_handler(
            endpoint="https://rcp.example/v1",
            api_key="k",
            payload={},
            headers_extra={},
            stream=False,
            full_url="https://rcp.example/v1/rerank",
            model="org/m",
            provider_label="EPFL RCP",
        )

    failing = AsyncMock(side_effect=BackendHTTPError(404, "<html>"))
    with patch.object(llm_service, "_execute_http_request", failing):
        with pytest.raises(BackendHTTPError) as caught:
            asyncio.run(call())
    assert caught.value.model == "org/m"
    assert caught.value.provider == "EPFL RCP"
