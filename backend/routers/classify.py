from fastapi import APIRouter, Depends, Request
from backend.middleware.auth import require_auth
from backend.middleware.body import json_body
from backend.middleware.model_id import require_namespaced_model
from backend.services.authorization_service import ensure_model_access
from backend.services.llm_service import llm_proxy_classify
from backend.services.route_service import resolve_route

router = APIRouter()

# vLLM serves /classify at the server root, NOT under /v1 like
# chat/completions/embeddings — hence server_root=True (see routers/rerank.py).


@router.post("/v1/classify")
async def classify(
    request: Request,
    token: str = Depends(require_auth),
    data: dict = Depends(json_body),
):
    public_model = require_namespaced_model(data.get("model"))
    await ensure_model_access(request.app.state.engine, token, public_model)
    route = await resolve_route(public_model, token, server_root=True)
    data["model"] = route.upstream_model(public_model)
    response = await llm_proxy_classify(
        endpoint=route.endpoint,
        api_key=route.api_key,
        payload=data,
        model=data["model"],
        provider_label=route.provider_label,
    )
    return route.restore_public_id(response.data)
