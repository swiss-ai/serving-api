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
from backend.services.metrics_service import metrics_collector
from backend.middleware.logging import AccessLogMiddleware
from backend.models.protocols import BackendHTTPError
from backend.routers import (
    admin_monitoring,
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


_UPSTREAM_DETAIL_LIMIT = 500


@app.exception_handler(BackendHTTPError)
async def backend_http_error_handler(request: Request, exc: BackendHTTPError):
    try:
        json.loads(exc.body)
    except (json.JSONDecodeError, TypeError):
        return _upstream_error_response(request, exc)
    return Response(
        content=exc.body, status_code=exc.status_code, media_type="application/json"
    )


def _upstream_error_response(request: Request, exc: BackendHTTPError) -> JSONResponse:
    """Wrap a non-JSON upstream error in the OpenAI envelope. Plain-text
    bodies are kept (truncated) as they often carry the reason, e.g. OpenTela's
    "No provider found"; HTML ones are a gateway's error page, so dropped."""
    message = f"upstream {exc.provider or 'server'} returned {exc.status_code}"
    if exc.model:
        message += f" for model {exc.model}"
    message += f" on {request.url.path}"
    detail = (exc.body or "").strip()
    if detail and not detail.startswith("<"):
        message += f": {detail[:_UPSTREAM_DETAIL_LIMIT]}"
    return _openai_error_response(exc.status_code, message, error_type="upstream_error")


def _openai_error_type(status_code: int) -> str:
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
    error_type=None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "message": message,
                "type": error_type or _openai_error_type(status_code),
                "param": param,
                "code": code,
            }
        },
        headers=headers,
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    detail = exc.detail
    message = detail if isinstance(detail, str) else json.dumps(detail)
    return _openai_error_response(
        exc.status_code,
        message,
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = exc.errors()
    first = errors[0] if errors else {}
    message = first.get("msg", "Invalid request")
    loc = [str(p) for p in first.get("loc", []) if p not in ("body", "query", "path")]
    param = ".".join(loc) if loc else None
    return _openai_error_response(422, message, param=param, code="invalid_request")


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logging.getLogger("backend").exception("Unhandled gateway error")
    return _openai_error_response(500, "Internal server error", code="internal_error")


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
app.include_router(admin_monitoring.router)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080, access_log=False)
