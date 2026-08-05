from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from backend.middleware.auth import require_auth
from backend.middleware.ratelimit import enforce_rate_limit
from backend.middleware.body import json_body
from backend.services.llm_service import llm_proxy_responses, response_generator_raw
from backend.services.passthrough_service import (
    resolve_model,
    endpoint as passthrough_endpoint,
)
from backend.config import get_settings

router = APIRouter()
settings = get_settings()


@router.post("/v1/responses")
async def create_response(
    token: str = Depends(require_auth),
    data: dict = Depends(json_body),
):
    stream = data.get("stream", False)
    model = data.get("model", "unknown")

    resolved = await resolve_model(model)
    if resolved is not None:
        # Only passthrough (externally-hosted) traffic is rate limited —
        # see _resolve_route in routers/completions.py.
        enforce_rate_limit(token)
        endpoint, api_key = (
            passthrough_endpoint(resolved.provider),
            resolved.provider.api_key,
        )
        # The upstream only knows the un-prefixed id.
        model = resolved.upstream_id
        data["model"] = resolved.upstream_id
    else:
        endpoint, api_key = settings.otela_head_addr + "/v1/service/llm/v1/", token

    response = await llm_proxy_responses(
        endpoint=endpoint,
        api_key=api_key,
        payload=data,
        stream=stream,
        model=model,
    )

    if stream:
        # Raw SSE passthrough: streamed Responses events keep the
        # upstream's un-prefixed model id (rewriting would mean parsing
        # every event; revisit if a client turns out to care).
        return StreamingResponse(
            response_generator_raw(response),
            media_type="text/event-stream",
            headers=response.headers,
        )
    if resolved is not None and isinstance(response.data, dict):
        if "model" in response.data:
            response.data["model"] = resolved.public_id
    return response.data
