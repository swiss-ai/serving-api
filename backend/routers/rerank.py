from fastapi import APIRouter, Depends, Request
from backend.middleware.auth import require_auth
from backend.middleware.body import json_body
from backend.middleware.model_id import require_namespaced_model
from backend.services.authorization_service import ensure_model_access
from backend.services.llm_service import llm_proxy_rerank, llm_proxy_score
from backend.services.route_service import resolve_route

router = APIRouter()

# vLLM serves the pooling-family endpoints (/score, /rerank, /pooling, /classify)
# at the server root, NOT under /v1 like chat/completions/embeddings. So the
# OpenTela upstream base here must be the server root (server_root=True),
# ".../v1/service/llm/". Appending "/v1/" would forward to a nonexistent
# "/v1/score" and 404. Passthrough providers always get the path as called.


@router.post("/v1/rerank")
async def rerank(
    request: Request,
    token: str = Depends(require_auth),
    data: dict = Depends(json_body),
):
    public_model = require_namespaced_model(data.get("model"))
    await ensure_model_access(request.app.state.engine, token, public_model)
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
    request: Request,
    token: str = Depends(require_auth),
    data: dict = Depends(json_body),
):
    public_model = require_namespaced_model(data.get("model"))
    await ensure_model_access(request.app.state.engine, token, public_model)
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
