from fastapi import APIRouter, Request, Depends
from fastapi.responses import StreamingResponse
from backend.middleware.auth import require_auth
from backend.services.llm_service import llm_proxy, llm_proxy_completions, response_generator
from backend.models.protocols import LLMRequest, LLMCompletionsRequest
from backend.config import get_settings

router = APIRouter()
settings = get_settings()

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
):
    data = await request.json()
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

    response = await llm_proxy(
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


@router.post("/v1/completions")
async def completion(
    request: Request,
    token: str = Depends(require_auth),
):
    data = await request.json()
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
