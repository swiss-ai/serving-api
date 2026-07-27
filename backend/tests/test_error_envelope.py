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
