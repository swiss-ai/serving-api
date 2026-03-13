import logging
import time
from contextlib import asynccontextmanager
from sqlmodel import create_engine
from typing import Annotated
from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import StreamingResponse
from starlette.middleware.base import BaseHTTPMiddleware
from backend.llm_proxy import (
    llm_proxy,
    response_generator,
    llm_proxy_completions,
    llm_proxy_embeddings,
)
from backend.config import get_settings
from backend.auth import (
    get_profile_from_accesstoken,
    get_or_create_apikey,
    verify_token,
)
from backend.provider import get_all_models
from backend.utils import get_statistics, get_ttl_hash, get_langfuse_metrics
from backend.protocols import LLMRequest, LLMCompletionsRequest
from functools import lru_cache
from fastapi.concurrency import run_in_threadpool
from backend.metrics import metrics_collector
from typing import Optional

engine = None
settings = get_settings()
security = HTTPBearer()
optional_security = HTTPBearer(auto_error=False)
RESERVED_KEYS = [
    "model",
    "messages",
    "stream",
    "stream_options",
    "logprobs",
    "top_logprobs",
    "max_tokens",
    "temperature",
    "top_p",
    "seed",
    "presence_penalty",
    "frequency_penalty",
    "user_id",
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    global engine
    engine = create_engine(
        settings.database_url,
        pool_pre_ping=True,
    )
    # Ensure metrics collector initialized (singleton, trigger init here)
    _ = metrics_collector
    yield
    engine = None


access_logger = logging.getLogger("access")


class AccessLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.time()
        response = await call_next(request)
        duration_ms = (time.time() - start) * 1000
        token = request.headers.get("authorization", "")
        if token.startswith("Bearer "):
            # Log last 8 chars only for identification without exposing full key
            token = f"...{token[-8:]}"
        else:
            token = "-"
        access_logger.info(
            "%s %s %s %d %.0fms",
            token,
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )
        return response


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


@app.post("/v1/chat/completions")
async def chat_completion(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
):
    token = credentials.credentials

    if not verify_token(engine, token):
        raise HTTPException(
            status_code=401,
            detail="Invalid access token",
        )

    data = await request.json()
    # Extract headers for tracking
    opt_out = request.headers.get("X-OPTOUT-TRACKING", "false").lower() in (
        "true",
        "1",
        "yes",
    )
    app_title = request.headers.get("X-Title", "")
    if "stream" not in data:
        data["stream"] = False
    if isinstance(data["stream"], str):
        if data["stream"].lower() == "true":
            data["stream"] = True  # convert to boolean
    if data["stream"]:
        data["stream_options"] = {"include_usage": True}
    # Construct LLMRequest
    reorg_data = {"extra_body": {}}
    for k, v in data.items():
        if k in RESERVED_KEYS:
            reorg_data[k] = v
        else:
            reorg_data["extra_body"][k] = v

    llm_request = LLMRequest(
        user_id=token, opt_out=opt_out, app_title=app_title, **reorg_data
    )

    response = await llm_proxy(
        endpoint=settings.ocf_head_addr + "/v1/service/llm/v1/",
        api_key=token,
        request=llm_request,
    )
    if "stream" in data and data["stream"]:

        async def stream_generator():
            # Pass metrics_ctx if available in response
            metrics_ctx = getattr(response, "metrics_ctx", None)
            async for chunk in response_generator(response, metrics_ctx=metrics_ctx):
                yield chunk

        return StreamingResponse(
            stream_generator(), media_type="text/event-stream", headers=response.headers
        )
    return response


@app.post("/v1/completions")
async def completion(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
):
    token = credentials.credentials

    if not verify_token(engine, token):
        raise HTTPException(
            status_code=401,
            detail="Invalid access token",
        )
    data = await request.json()

    # Extract headers for tracking
    opt_out = request.headers.get("X-OPTOUT-TRACKING", "").lower() in (
        "true",
        "1",
        "yes",
    )
    app_title = request.headers.get("X-Title", "")

    if "stream" not in data:
        data["stream"] = False
    if isinstance(data["stream"], str):
        if data["stream"].lower() == "true":
            data["stream"] = True
    if data["stream"]:
        data["stream_options"] = {"include_usage": True}

    completion_reserved = [
        "model",
        "prompt",
        "stream",
        "stream_options",
        "max_tokens",
        "temperature",
        "top_p",
        "seed",
        "presence_penalty",
        "frequency_penalty",
        "user_id",
    ]

    reorg_data = {"extra_body": {}}
    for k, v in data.items():
        if k in completion_reserved:
            reorg_data[k] = v
        else:
            reorg_data["extra_body"][k] = v

    llm_request = LLMCompletionsRequest(
        user_id=token, opt_out=opt_out, app_title=app_title, **reorg_data
    )

    response = await llm_proxy_completions(
        endpoint=settings.ocf_head_addr + "/v1/service/llm/v1/",
        api_key=token,
        request=llm_request,
    )
    if "stream" in data and data["stream"]:

        async def stream_generator():
            metrics_ctx = getattr(response, "metrics_ctx", None)
            async for chunk in response_generator(response, metrics_ctx=metrics_ctx):
                yield chunk

        return StreamingResponse(
            stream_generator(), media_type="text/event-stream", headers=response.headers
        )
    return response


@app.post("/v1/embeddings")
async def embeddings(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
):
    token = credentials.credentials

    if not verify_token(engine, token):
        raise HTTPException(
            status_code=401,
            detail="Invalid access token",
        )
    data = await request.json()
    data["user_id"] = token

    # Extract headers for tracking
    opt_out = request.headers.get("X-OPTOUT-TRACKING", "").lower() in (
        "true",
        "1",
        "yes",
    )
    app_title = request.headers.get("X-Title", "")

    data["opt_out"] = opt_out
    data["app_title"] = app_title

    response = await llm_proxy_embeddings(
        endpoint=settings.ocf_head_addr + "/v1/service/llm/v1/",
        api_key=token,
        **data,
    )
    return response


@app.get("/v1/models_detailed")
async def list_models_detailed():
    models = get_all_models(settings.ocf_head_addr + "/v1/dnt/table", with_details=True)
    return dict(
        object="list",
        data=models,
    )


@app.get("/v1/models")
async def list_models():
    models = get_all_models(
        settings.ocf_head_addr + "/v1/dnt/table", with_details=False
    )
    return dict(
        object="list",
        data=models,
    )


@app.get("/v1/profile")
async def get_profile(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)] = None,
):
    try:
        user_profile = get_profile_from_accesstoken(credentials.credentials)
        if user_profile:
            api_key = get_or_create_apikey(engine, user_profile["email"])
        user_profile["api_key"] = api_key.key
        user_profile["budget"] = api_key.budget
        return user_profile
    except Exception:
        return HTTPException(
            status_code=401,
            detail="Invalid access token",
        )


@app.get("/v1/statistics")
async def get_statistics_endpoint(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(optional_security)
    ] = None,
):
    if credentials:
        api_key = credentials.credentials
    else:
        api_key = None
    stats = get_statistics(api_key, ttl_hash=get_ttl_hash())
    return stats


@app.post("/v1/metrics")
async def get_metrics_endpoint(
    request: Request,
):
    res = await request.json()
    # Use ttl_hash to cache results for 1 hour
    ttl = get_ttl_hash(seconds=3600)
    try:
        data = await get_langfuse_metrics(res, ttl_hash=ttl)
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@lru_cache(maxsize=32)
def get_perf_data(model: Optional[str] = None, ttl_hash: int = None):
    # This function is cached. ttl_hash argument is used to control cache invalidation.
    return metrics_collector.get_benchmark_data(model)


@app.get("/v1/perf")
async def get_perf_endpoint(request: Request):
    # Use ttl_hash to cache results for 1 hour (or adjusted as per requirement)
    # Reusing get_ttl_hash which defaults to 24h, let's use 1h like metrics endpoint
    ttl = get_ttl_hash()

    # Extract model query param
    model = request.query_params.get("model")

    # Run synchronous cached function in threadpool to avoid blocking event loop
    data = await run_in_threadpool(get_perf_data, model=model, ttl_hash=ttl)
    return dict(object="list", data=data)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080)
