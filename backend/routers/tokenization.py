from fastapi import APIRouter, Depends, Request
from backend.middleware.auth import require_auth
from backend.middleware.body import json_body
from backend.services.authorization_service import ensure_model_access
from backend.services.llm_service import llm_proxy_tokenize, llm_proxy_detokenize
from backend.config import get_settings

router = APIRouter()
settings = get_settings()

# vLLM serves /tokenize and /detokenize at the server root, NOT under /v1 like
# chat/completions/embeddings. The upstream base must stop at ".../v1/service/llm/" —
# appending "/v1/" would forward to a nonexistent "/v1/tokenize" on the pod and 404.


@router.post("/v1/tokenize")
async def tokenize(
    request: Request,
    token: str = Depends(require_auth),
    data: dict = Depends(json_body),
):
    await ensure_model_access(
        request.app.state.engine, token, data.get("model", "unknown")
    )
    response = await llm_proxy_tokenize(
        endpoint=settings.otela_head_addr + "/v1/service/llm/",
        api_key=token,
        payload=data,
        model=data.get("model", "unknown"),
    )
    return response.data


@router.post("/v1/detokenize")
async def detokenize(
    request: Request,
    token: str = Depends(require_auth),
    data: dict = Depends(json_body),
):
    await ensure_model_access(
        request.app.state.engine, token, data.get("model", "unknown")
    )
    response = await llm_proxy_detokenize(
        endpoint=settings.otela_head_addr + "/v1/service/llm/",
        api_key=token,
        payload=data,
        model=data.get("model", "unknown"),
    )
    return response.data
