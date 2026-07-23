"""#76 — gateway-originated errors must use the OpenAI error envelope
`{"error": {"message", "type", "param", "code"}}` instead of FastAPI's
default `{"detail": ...}` shape.

These tests mount the real exception handlers from `backend.main` onto a
minimal app so they exercise the actual handler logic without needing the
database-backed app fixture.
"""

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.main import (
    _openai_error_type,
    http_exception_handler,
    validation_exception_handler,
)


def _make_client() -> TestClient:
    app = FastAPI()
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)

    @app.get("/boom")
    def boom():
        raise HTTPException(status_code=401, detail="Invalid access token")

    class Body(BaseModel):
        x: int

    @app.post("/validate")
    def validate(body: Body):
        return {"ok": True}

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


def test_error_type_mapping():
    assert _openai_error_type(400) == "invalid_request_error"
    assert _openai_error_type(401) == "authentication_error"
    assert _openai_error_type(403) == "permission_error"
    assert _openai_error_type(404) == "invalid_request_error"
    assert _openai_error_type(422) == "invalid_request_error"
    assert _openai_error_type(429) == "rate_limit_error"
    assert _openai_error_type(500) == "api_error"
    assert _openai_error_type(503) == "api_error"
