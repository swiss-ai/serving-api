import json
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from sqlmodel import create_engine
from backend.config import get_settings
from backend.services.metrics_service import metrics_collector
from backend.middleware.logging import AccessLogMiddleware
from backend.errors import (
    http_exception_handler,
    unhandled_exception_handler,
    validation_exception_handler,
)
from backend.models.protocols import BackendHTTPError
from backend.routers import (
    admin_monitoring,
    completions,
    model_access,
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


@app.exception_handler(BackendHTTPError)
async def backend_http_error_handler(request: Request, exc: BackendHTTPError):
    try:
        json.loads(exc.body)
        media_type = "application/json"
        content = exc.body
    except (json.JSONDecodeError, TypeError):
        media_type = "text/plain"
        content = exc.body
    return Response(content=content, status_code=exc.status_code, media_type=media_type)


# Registered here, defined in backend.errors — see that module for why the
# handlers must not live next to the app's import-time settings.
app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(Exception, unhandled_exception_handler)


app.include_router(completions.router)
app.include_router(responses.router)
app.include_router(embeddings.router)
app.include_router(models.router)
app.include_router(model_access.router)
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
