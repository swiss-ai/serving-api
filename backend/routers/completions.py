import time

from fastapi import APIRouter, Request, Depends
from fastapi.responses import StreamingResponse
from backend.middleware.auth import require_auth
from backend.middleware.body import json_body
from backend.middleware.model_id import require_namespaced_model
from backend.services.langfuse_service import (
    prepare_stream_trace,
    record_if_monitored,
)
from backend.services.monitoring_service import resolve_owner_email
from backend.services.usage_service import record_usage
from backend.services.llm_service import (
    llm_proxy,
    llm_proxy_completions,
    response_generator,
)
from backend.services.route_service import resolve_route
from backend.models.protocols import LLMRequest, LLMCompletionsRequest

router = APIRouter()


def _record_usage(engine, token, public_model, response) -> None:
    """Count a non-streamed request against its owner.

    Usage accounting is deliberately independent of Langfuse tracing: it
    covers every request and keeps working when tracing is narrowed to
    monitored users only.
    """
    usage = getattr(response, "usage", None)
    if usage is None:
        data = getattr(response, "data", None)
        usage = data.get("usage") if isinstance(data, dict) else None
    if usage is None:
        return
    if isinstance(usage, dict):
        prompt, completion = usage.get("prompt_tokens"), usage.get("completion_tokens")
    else:
        prompt = getattr(usage, "prompt_tokens", None)
        completion = getattr(usage, "completion_tokens", None)
    email = resolve_owner_email(engine, token)
    if not email:
        return
    record_usage(
        owner_email=email,
        model=public_model,
        prompt_tokens=prompt or 0,
        completion_tokens=completion or 0,
        engine=engine,
    )


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
    token: str = Depends(require_auth),
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

    require_namespaced_model(llm_request.model)
    # Passthrough ids go to the provider, everything else to OpenTela —
    # see backend/services/route_service.py. Traces/monitoring keep the
    # public (prefixed) id the client asked for; only the forwarded
    # request carries the upstream's own id.
    route = await resolve_route(llm_request.model, token)
    resolved = route.resolved
    public_model = llm_request.model
    llm_request.model = route.upstream_model(public_model)
    trace_ctx = None
    if data["stream"]:
        # Streamed: the complete trace (output/usage/TTFT included) is
        # emitted by response_generator after the last chunk.
        trace_ctx = prepare_stream_trace(
            request.app.state.engine,
            api_key=token,
            model=public_model,
            request_data=data,
            app_title=app_title,
        )
    proxy_started = time.monotonic()
    response = await llm_proxy(
        endpoint=route.endpoint,
        api_key=route.api_key,
        request=llm_request,
        provider_label=route.provider_label,
    )
    if not data["stream"]:
        record_if_monitored(
            request.app.state.engine,
            api_key=token,
            model=public_model,
            request_data=data,
            response_data=getattr(response, "data", None) or response,
            streamed=False,
            app_title=app_title,
            latency_ms=(time.monotonic() - proxy_started) * 1000,
        )
        _record_usage(request.app.state.engine, token, public_model, response)
    if "stream" in data and data["stream"]:
        model_override = resolved.public_id if resolved is not None else None

        async def stream_generator():
            metrics_ctx = getattr(response, "metrics_ctx", None)
            async for chunk in response_generator(
                response,
                metrics_ctx=metrics_ctx,
                trace_ctx=trace_ctx,
                model_override=model_override,
            ):
                yield chunk

        return StreamingResponse(
            stream_generator(), media_type="text/event-stream", headers=response.headers
        )
    if resolved is not None:
        response.model = resolved.public_id
    return response


@router.post("/v1/completions")
async def completion(
    request: Request,
    token: str = Depends(require_auth),
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

    require_namespaced_model(llm_request.model)
    # Passthrough ids go to the provider, everything else to OpenTela —
    # see backend/services/route_service.py. Traces/monitoring keep the
    # public (prefixed) id the client asked for; only the forwarded
    # request carries the upstream's own id.
    route = await resolve_route(llm_request.model, token)
    resolved = route.resolved
    public_model = llm_request.model
    llm_request.model = route.upstream_model(public_model)
    trace_ctx = None
    if data["stream"]:
        # Streamed: the complete trace (output/usage/TTFT included) is
        # emitted by response_generator after the last chunk.
        trace_ctx = prepare_stream_trace(
            request.app.state.engine,
            api_key=token,
            model=public_model,
            request_data=data,
            app_title=app_title,
        )
    proxy_started = time.monotonic()
    response = await llm_proxy_completions(
        endpoint=route.endpoint,
        api_key=route.api_key,
        request=llm_request,
        provider_label=route.provider_label,
    )
    if not data["stream"]:
        record_if_monitored(
            request.app.state.engine,
            api_key=token,
            model=public_model,
            request_data=data,
            response_data=getattr(response, "data", None) or response,
            streamed=False,
            app_title=app_title,
            latency_ms=(time.monotonic() - proxy_started) * 1000,
        )
        _record_usage(request.app.state.engine, token, public_model, response)
    if "stream" in data and data["stream"]:
        model_override = resolved.public_id if resolved is not None else None

        async def stream_generator():
            metrics_ctx = getattr(response, "metrics_ctx", None)
            async for chunk in response_generator(
                response,
                metrics_ctx=metrics_ctx,
                trace_ctx=trace_ctx,
                model_override=model_override,
            ):
                yield chunk

        return StreamingResponse(
            stream_generator(), media_type="text/event-stream", headers=response.headers
        )
    if resolved is not None:
        response.model = resolved.public_id
    return response
