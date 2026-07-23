import json
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from sqlmodel import create_engine
from backend.config import get_settings
from backend.services.auth_service import dev_bypass_enabled
from backend.services.metrics_service import metrics_collector
from backend.middleware.logging import AccessLogMiddleware
from backend.models.protocols import BackendHTTPError
from backend.routers import (
    completions,
    responses,
    embeddings,
    models,
    profile,
    metrics,
    rerank,
    classify,
    tokenization,
    mcp,
)

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.engine = create_engine(
        settings.database_url,
        pool_pre_ping=True,
    )
    if dev_bypass_enabled():
        logging.getLogger("backend").warning(
            "DEV AUTH BYPASS ENABLED — Auth0 is skipped for the dev token. "
            "This must never be enabled in a deployed environment."
        )
    _ = metrics_collector
    yield
    app.state.engine = None


app = FastAPI(lifespan=lifespan)

if settings.access_log:
    logging.basicConfig(level=logging.INFO)
    app.add_middleware(AccessLogMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(BackendHTTPError)
async def backend_http_error_handler(request: Request, exc: BackendHTTPError):
    # Upstream (vllm/sglang) already emits a proper OpenAI error envelope —
    # pass it through untouched.
    try:
        json.loads(exc.body)
        media_type = "application/json"
        content = exc.body
    except (json.JSONDecodeError, TypeError):
        media_type = "text/plain"
        content = exc.body
    return Response(content=content, status_code=exc.status_code, media_type=media_type)


def _openai_error_type(status_code: int) -> str:
    """Map an HTTP status to the OpenAI error `type`."""
    if status_code == 429:
        return "rate_limit_error"
    if status_code >= 500:
        return "api_error"
    if status_code == 401:
        return "authentication_error"
    if status_code == 403:
        return "permission_error"
    return "invalid_request_error"


def _openai_error_response(
    status_code: int,
    message: str,
    *,
    code=None,
    param=None,
    headers=None,
) -> JSONResponse:
    """Wrap a gateway-originated error in the OpenAI error envelope so clients
    that parse `error.message` (openai SDK error classes, most wrappers) get a
    useful payload instead of FastAPI's default `{"detail": ...}` shape (#76)."""
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "message": message,
                "type": _openai_error_type(status_code),
                "param": param,
                "code": code,
            }
        },
        headers=headers,
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    # Gateway-originated HTTP errors (auth failures, 404s, ...) — FastAPI's
    # default `{"detail": ...}` isn't the OpenAI shape (#76).
    detail = exc.detail
    message = detail if isinstance(detail, str) else json.dumps(detail)
    return _openai_error_response(
        exc.status_code,
        message,
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    # Malformed request bodies/params → 422 in the OpenAI envelope (#76).
    errors = exc.errors()
    first = errors[0] if errors else {}
    message = first.get("msg", "Invalid request")
    # Point `param` at the offending field when we can identify it.
    loc = [str(p) for p in first.get("loc", []) if p not in ("body", "query", "path")]
    param = ".".join(loc) if loc else None
    return _openai_error_response(422, message, param=param, code="invalid_request")


app.include_router(completions.router)
app.include_router(responses.router)
app.include_router(embeddings.router)
app.include_router(models.router)
app.include_router(profile.router)
app.include_router(metrics.router)
app.include_router(rerank.router)
app.include_router(classify.router)
app.include_router(tokenization.router)
app.include_router(mcp.router)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080, access_log=False)
