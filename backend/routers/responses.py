from fastapi import APIRouter, Request, Depends
from fastapi.responses import StreamingResponse
from backend.middleware.auth import require_auth
from backend.services.llm_service import llm_proxy_responses, response_generator_raw
from backend.services.cscs_l1_service import is_l1_model, l1_endpoint, l1_api_key
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
    model = data.get("model", "unknown")

    if await is_l1_model(model):
        endpoint, api_key = l1_endpoint(), l1_api_key()
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
