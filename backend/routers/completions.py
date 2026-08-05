import time

from fastapi import APIRouter, Request, Depends
from fastapi.responses import StreamingResponse
from backend.middleware.ratelimit import rate_limited
from backend.middleware.body import json_body
from backend.services.langfuse_service import (
    prepare_stream_trace,
    record_if_monitored,
)
from backend.services.llm_service import (
    llm_proxy,
    llm_proxy_completions,
    response_generator,
)
from backend.services.passthrough_service import (
    resolve_provider,
    endpoint as passthrough_endpoint,
)
from backend.models.protocols import LLMRequest, LLMCompletionsRequest
from backend.config import get_settings

router = APIRouter()
settings = get_settings()


async def _resolve_endpoint_and_key(
    model: str, user_token: str
) -> tuple[str, str, str]:
    """Models hosted by a passthrough provider (CSCS L1, RCP, ...) go to
    that provider's upstream endpoint with its shared key; everything else
    stays on the OpenTela proxy with the user's bearer token forwarded
    as-is. The third element is the provider's display label (None for
    OpenTela) — recorded as the perf "served on" dimension."""
    provider = await resolve_provider(model)
    if provider is not None:
        return passthrough_endpoint(provider), provider.api_key, provider.device
    return settings.otela_head_addr + "/v1/service/llm/v1/", user_token, None


CHAT_RESERVED_KEYS = [
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

COMPLETION_RESERVED_KEYS = [
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


@router.post("/v1/chat/completions")
async def chat_completion(
    request: Request,
    token: str = Depends(rate_limited),
    data: dict = Depends(json_body),
):
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
            data["stream"] = True
    if data["stream"]:
        data["stream_options"] = {"include_usage": True}

    reorg_data = {"extra_body": {}}
    for k, v in data.items():
        if k in CHAT_RESERVED_KEYS:
            reorg_data[k] = v
        else:
            reorg_data["extra_body"][k] = v

    llm_request = LLMRequest(
        user_id=token, opt_out=opt_out, app_title=app_title, **reorg_data
    )

    endpoint, api_key, provider_label = await _resolve_endpoint_and_key(
        llm_request.model, token
    )
    trace_ctx = None
    if data["stream"]:
        # Streamed: the complete trace (output/usage/TTFT included) is
        # emitted by response_generator after the last chunk.
        trace_ctx = prepare_stream_trace(
            request.app.state.engine,
            api_key=token,
            model=llm_request.model,
            request_data=data,
            app_title=app_title,
        )
    proxy_started = time.monotonic()
    response = await llm_proxy(
        endpoint=endpoint,
        api_key=api_key,
        request=llm_request,
        provider_label=provider_label,
    )
    if not data["stream"]:
        record_if_monitored(
            request.app.state.engine,
            api_key=token,
            model=llm_request.model,
            request_data=data,
            response_data=getattr(response, "data", None) or response,
            streamed=False,
            app_title=app_title,
            latency_ms=(time.monotonic() - proxy_started) * 1000,
        )
    if "stream" in data and data["stream"]:

        async def stream_generator():
            metrics_ctx = getattr(response, "metrics_ctx", None)
            async for chunk in response_generator(
                response, metrics_ctx=metrics_ctx, trace_ctx=trace_ctx
            ):
                yield chunk

        return StreamingResponse(
            stream_generator(), media_type="text/event-stream", headers=response.headers
        )
    return response


@router.post("/v1/completions")
async def completion(
    request: Request,
    token: str = Depends(rate_limited),
    data: dict = Depends(json_body),
):
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

    reorg_data = {"extra_body": {}}
    for k, v in data.items():
        if k in COMPLETION_RESERVED_KEYS:
            reorg_data[k] = v
        else:
            reorg_data["extra_body"][k] = v

    llm_request = LLMCompletionsRequest(
        user_id=token, opt_out=opt_out, app_title=app_title, **reorg_data
    )

    endpoint, api_key, provider_label = await _resolve_endpoint_and_key(
        llm_request.model, token
    )
    trace_ctx = None
    if data["stream"]:
        # Streamed: the complete trace (output/usage/TTFT included) is
        # emitted by response_generator after the last chunk.
        trace_ctx = prepare_stream_trace(
            request.app.state.engine,
            api_key=token,
            model=llm_request.model,
            request_data=data,
            app_title=app_title,
        )
    proxy_started = time.monotonic()
    response = await llm_proxy_completions(
        endpoint=endpoint,
        api_key=api_key,
        request=llm_request,
        provider_label=provider_label,
    )
    if not data["stream"]:
        record_if_monitored(
            request.app.state.engine,
            api_key=token,
            model=llm_request.model,
            request_data=data,
            response_data=getattr(response, "data", None) or response,
            streamed=False,
            app_title=app_title,
            latency_ms=(time.monotonic() - proxy_started) * 1000,
        )
    if "stream" in data and data["stream"]:

        async def stream_generator():
            metrics_ctx = getattr(response, "metrics_ctx", None)
            async for chunk in response_generator(
                response, metrics_ctx=metrics_ctx, trace_ctx=trace_ctx
            ):
                yield chunk

        return StreamingResponse(
            stream_generator(), media_type="text/event-stream", headers=response.headers
        )
    return response
