"""The OpenAI-shaped error envelope every gateway error is rendered into.

Split out of ``backend.main`` so it can be imported without importing the
app. ``main`` reads settings at import time and freezes them on a module
global that its lifespan later builds the database engine from, so anything
that imports ``main`` early — a test module, a script — pins that global to
whatever the environment happened to say at the time. Tests that configure a
database and then import the app depend on being the first to do so, and
importing ``main`` merely to reach these helpers used to break them.

Clients parse `error.message`, so this shape is API surface: keep it in step
with the OpenAI error schema rather than adding fields to taste.
"""

import json
import logging

from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request


def openai_error_type(status_code: int) -> str:
    if status_code == 429:
        return "rate_limit_error"
    if status_code >= 500:
        return "api_error"
    if status_code == 401:
        return "authentication_error"
    if status_code == 403:
        return "permission_error"
    return "invalid_request_error"


def openai_error_response(
    status_code: int,
    message: str,
    *,
    code=None,
    param=None,
    headers=None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "message": message,
                "type": openai_error_type(status_code),
                "param": param,
                "code": code,
            }
        },
        headers=headers,
    )


async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    detail = exc.detail
    message = detail if isinstance(detail, str) else json.dumps(detail)
    return openai_error_response(
        exc.status_code,
        message,
        headers=getattr(exc, "headers", None),
    )


async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = exc.errors()
    first = errors[0] if errors else {}
    message = first.get("msg", "Invalid request")
    loc = [str(p) for p in first.get("loc", []) if p not in ("body", "query", "path")]
    param = ".".join(loc) if loc else None
    return openai_error_response(422, message, param=param, code="invalid_request")


async def unhandled_exception_handler(request: Request, exc: Exception):
    logging.getLogger("backend").exception("Unhandled gateway error")
    return openai_error_response(500, "Internal server error", code="internal_error")
