from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from backend.middleware.auth import require_auth
from backend.middleware.ratelimit import enforce_rate_limit
from backend.middleware.body import json_body
from backend.middleware.model_id import require_namespaced_model
from backend.services.authorization_service import ensure_model_access
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
    request: Request,
    token: str = Depends(require_auth),
    data: dict = Depends(json_body),
):
    stream = data.get("stream", False)
    model = require_namespaced_model(data.get("model", "unknown"))

    # Before any rewriting: the check keys off the id the caller asked for
    # (and, for SwissAI-Research/ ids, its upstream form).
    await ensure_model_access(request.app.state.engine, token, model)
    resolved = await resolve_model(model)
    if resolved is not None:
        # The serving side only knows the un-prefixed id.
        model = resolved.upstream_id
        data["model"] = resolved.upstream_id
    if resolved is not None and resolved.provider is not None:
        # Only passthrough (externally-hosted) traffic is rate limited —
        # see _resolve_route in routers/completions.py.
        enforce_rate_limit(token)
        endpoint, api_key = (
            passthrough_endpoint(resolved.provider),
            resolved.provider.api_key,
        )
    else:
        # Bare ids and SwissAI-Research/ (our own namespace) → OpenTela.
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
