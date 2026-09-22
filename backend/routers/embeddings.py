from fastapi import APIRouter, Request, Depends
from backend.middleware.auth import require_auth
from backend.middleware.body import json_body
from backend.middleware.model_id import require_namespaced_model
from backend.services.llm_service import llm_proxy_embeddings
from backend.services.route_service import resolve_route

router = APIRouter()


@router.post("/v1/embeddings")
async def embeddings(
    request: Request,
    token: str = Depends(require_auth),
    data: dict = Depends(json_body),
):
    public_model = require_namespaced_model(data.get("model"))
    # Prefixed passthrough ids (RCP-AIaaS/..., CSCS-Inference/...) go to
    # the provider; everything else stays on OpenTela. Only the forwarded
    # request carries the upstream's own id.
    route = await resolve_route(public_model, token)
    data["model"] = route.upstream_model(public_model)
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
        endpoint=route.endpoint,
        api_key=route.api_key,
        provider_label=route.provider_label,
        **data,
    )
    if route.resolved is not None:
        response.model = route.resolved.public_id
    return response
