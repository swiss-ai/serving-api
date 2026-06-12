from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from backend.middleware.auth import require_auth
from backend.middleware.body import json_body
from backend.services.llm_service import llm_proxy_responses, response_generator_raw
from backend.services.passthrough_service import (
    resolve_provider,
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

    provider = await resolve_provider(model)
    if provider is not None:
        endpoint, api_key = passthrough_endpoint(provider), provider.api_key
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
        return StreamingResponse(
            response_generator_raw(response),
            media_type="text/event-stream",
            headers=response.headers,
        )
    return response.data
