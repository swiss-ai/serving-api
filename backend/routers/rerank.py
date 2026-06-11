from fastapi import APIRouter, Request, Depends
from backend.middleware.auth import require_auth
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
):
    data = await request.json()
    response = await llm_proxy_rerank(
        endpoint=settings.otela_head_addr + "/v1/service/llm/",
        api_key=token,
        payload=data,
        model=data.get("model", "unknown"),
    )
    return response.data


@router.post("/v1/score")
async def score(
    request: Request,
    token: str = Depends(require_auth),
):
    data = await request.json()
    response = await llm_proxy_score(
        endpoint=settings.otela_head_addr + "/v1/service/llm/",
        api_key=token,
        payload=data,
        model=data.get("model", "unknown"),
    )
    return response.data
