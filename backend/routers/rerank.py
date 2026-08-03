from fastapi import APIRouter, Depends, Request
from backend.middleware.auth import require_auth
from backend.middleware.body import json_body
from backend.middleware.model_id import require_namespaced_model
from backend.services.authorization_service import ensure_model_access
from backend.services.llm_service import llm_proxy_rerank, llm_proxy_score
from backend.config import get_settings

router = APIRouter()
settings = get_settings()

# vLLM serves the pooling-family endpoints (/score, /rerank, /pooling, /classify)
# at the server root, NOT under /v1 like chat/completions/embeddings. So the upstream
# base here must stop at ".../v1/service/llm/" — appending "/v1/" would forward to a
# nonexistent "/v1/score" on the model pod and 404.


@router.post("/v1/rerank")
async def rerank(
    request: Request,
    token: str = Depends(require_auth),
    data: dict = Depends(json_body),
):
    await ensure_model_access(
        request.app.state.engine, token, data.get("model", "unknown")
    )
    response = await llm_proxy_rerank(
        endpoint=settings.otela_head_addr + "/v1/service/llm/",
        api_key=token,
        payload=data,
        model=require_namespaced_model(data.get("model")),
    )
    return response.data


@router.post("/v1/score")
async def score(
    request: Request,
    token: str = Depends(require_auth),
    data: dict = Depends(json_body),
):
    await ensure_model_access(
        request.app.state.engine, token, data.get("model", "unknown")
    )
    response = await llm_proxy_score(
        endpoint=settings.otela_head_addr + "/v1/service/llm/",
        api_key=token,
        payload=data,
        model=require_namespaced_model(data.get("model")),
    )
    return response.data
