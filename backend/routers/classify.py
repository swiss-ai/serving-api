from fastapi import APIRouter, Depends
from backend.middleware.auth import require_auth
from backend.middleware.body import json_body
from backend.middleware.model_id import require_namespaced_model
from backend.services.llm_service import llm_proxy_classify
from backend.config import get_settings

router = APIRouter()
settings = get_settings()

# vLLM serves /classify at the server root, NOT under /v1 like
# chat/completions/embeddings. The upstream base must stop at ".../v1/service/llm/" —
# appending "/v1/" would forward to a nonexistent "/v1/classify" on the pod and 404.


@router.post("/v1/classify")
async def classify(
    token: str = Depends(require_auth),
    data: dict = Depends(json_body),
):
    response = await llm_proxy_classify(
        endpoint=settings.otela_head_addr + "/v1/service/llm/",
        api_key=token,
        payload=data,
        model=require_namespaced_model(data.get("model")),
    )
    return response.data
