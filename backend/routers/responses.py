from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from backend.middleware.auth import require_auth
from backend.middleware.body import json_body
from backend.middleware.model_id import require_namespaced_model
from backend.services.llm_service import llm_proxy_responses, response_generator_raw
from backend.services.route_service import resolve_route

router = APIRouter()


@router.post("/v1/responses")
async def create_response(
    token: str = Depends(require_auth),
    data: dict = Depends(json_body),
):
    stream = data.get("stream", False)
    public_model = require_namespaced_model(data.get("model", "unknown"))

    # Passthrough ids go to the provider (rate limited there); bare ids and
    # SwissAI-Research/ (our own namespace) stay on OpenTela. The serving
    # side only knows the un-prefixed id.
    route = await resolve_route(public_model, token)
    model = route.upstream_model(public_model)
    data["model"] = model

    response = await llm_proxy_responses(
        endpoint=route.endpoint,
        api_key=route.api_key,
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
    return route.restore_public_id(response.data)
