from fastapi import APIRouter, Request, Depends
from fastapi.responses import StreamingResponse
from backend.middleware.auth import require_auth
from backend.services.llm_service import llm_proxy_responses, response_generator_raw
from backend.config import get_settings

router = APIRouter()
settings = get_settings()


@router.post("/v1/responses")
async def create_response(
    request: Request,
    token: str = Depends(require_auth),
):
    data = await request.json()
    stream = data.get("stream", False)

    response = await llm_proxy_responses(
        endpoint=settings.ocf_head_addr + "/v1/service/llm/v1/",
        api_key=token,
        payload=data,
        stream=stream,
        model=data.get("model", "unknown"),
    )

    if stream:
        return StreamingResponse(
            response_generator_raw(response),
            media_type="text/event-stream",
            headers=response.headers,
        )
    return response.data
