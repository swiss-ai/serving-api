from fastapi import APIRouter, Request, Depends
from backend.middleware.auth import require_auth
from backend.services.llm_service import llm_proxy_tokenize, llm_proxy_detokenize
from backend.config import get_settings

router = APIRouter()
settings = get_settings()


@router.post("/v1/tokenize")
async def tokenize(
    request: Request,
    token: str = Depends(require_auth),
):
    data = await request.json()
    response = await llm_proxy_tokenize(
        endpoint=settings.ocf_head_addr + "/v1/service/llm/v1/",
        api_key=token,
        payload=data,
        model=data.get("model", "unknown"),
    )
    return response.data


@router.post("/v1/detokenize")
async def detokenize(
    request: Request,
    token: str = Depends(require_auth),
):
    data = await request.json()
    response = await llm_proxy_detokenize(
        endpoint=settings.ocf_head_addr + "/v1/service/llm/v1/",
        api_key=token,
        payload=data,
        model=data.get("model", "unknown"),
    )
    return response.data
