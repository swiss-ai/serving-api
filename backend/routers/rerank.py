from fastapi import APIRouter, Depends
from backend.middleware.auth import require_auth
from backend.middleware.body import json_body
from backend.middleware.model_id import require_namespaced_model
from backend.services.llm_service import llm_proxy_rerank, llm_proxy_score
from backend.services.route_service import resolve_route

router = APIRouter()

# vLLM serves the pooling-family endpoints (/score, /rerank, /pooling, /classify)
# at the server root, NOT under /v1 like chat/completions/embeddings. So the
# upstream base here must be the server root (server_root=True): on OpenTela
# ".../v1/service/llm/", on a passthrough provider its base URL minus "/v1".
# Appending "/v1/" would forward to a nonexistent "/v1/score" and 404.


@router.post("/v1/rerank")
async def rerank(
    token: str = Depends(require_auth),
    data: dict = Depends(json_body),
):
    public_model = require_namespaced_model(data.get("model"))
    route = await resolve_route(public_model, token, server_root=True)
    data["model"] = route.upstream_model(public_model)
    response = await llm_proxy_rerank(
        endpoint=route.endpoint,
        api_key=route.api_key,
        payload=data,
        model=data["model"],
        provider_label=route.provider_label,
    )
    return route.restore_public_id(response.data)


@router.post("/v1/score")
async def score(
    token: str = Depends(require_auth),
    data: dict = Depends(json_body),
):
    public_model = require_namespaced_model(data.get("model"))
    route = await resolve_route(public_model, token, server_root=True)
    data["model"] = route.upstream_model(public_model)
    response = await llm_proxy_score(
        endpoint=route.endpoint,
        api_key=route.api_key,
        payload=data,
        model=data["model"],
        provider_label=route.provider_label,
    )
    return route.restore_public_id(response.data)
