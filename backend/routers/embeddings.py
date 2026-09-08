from fastapi import APIRouter, Request, Depends
from backend.middleware.auth import require_auth
from backend.middleware.body import json_body
from backend.middleware.model_id import require_namespaced_model
from backend.services.authorization_service import ensure_model_access
from backend.services.llm_service import llm_proxy_embeddings
from backend.config import get_settings

router = APIRouter()
settings = get_settings()


@router.post("/v1/embeddings")
async def embeddings(
    request: Request,
    token: str = Depends(require_auth),
    data: dict = Depends(json_body),
):
    require_namespaced_model(data.get("model"))
    data["user_id"] = token

    opt_out = request.headers.get("X-OPTOUT-TRACKING", "").lower() in (
        "true",
        "1",
        "yes",
    )
    app_title = request.headers.get("X-Title", "")

    data["opt_out"] = opt_out
    data["app_title"] = app_title

    await ensure_model_access(
        request.app.state.engine, token, data.get("model", "unknown")
    )
    response = await llm_proxy_embeddings(
        endpoint=settings.otela_head_addr + "/v1/service/llm/v1/",
        api_key=token,
        **data,
    )
    return response
