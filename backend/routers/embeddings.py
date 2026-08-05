from fastapi import APIRouter, Request, Depends
from backend.middleware.ratelimit import rate_limited
from backend.middleware.body import json_body
from backend.services.llm_service import llm_proxy_embeddings
from backend.config import get_settings

router = APIRouter()
settings = get_settings()


@router.post("/v1/embeddings")
async def embeddings(
    request: Request,
    token: str = Depends(rate_limited),
    data: dict = Depends(json_body),
):
    data["user_id"] = token

    opt_out = request.headers.get("X-OPTOUT-TRACKING", "").lower() in (
        "true",
        "1",
        "yes",
    )
    app_title = request.headers.get("X-Title", "")

    data["opt_out"] = opt_out
    data["app_title"] = app_title

    response = await llm_proxy_embeddings(
        endpoint=settings.otela_head_addr + "/v1/service/llm/v1/",
        api_key=token,
        **data,
    )
    return response
