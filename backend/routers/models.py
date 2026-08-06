from fastapi import APIRouter
from backend.services.model_service import get_all_models, platform_namespaced
from backend.services.passthrough_service import get_synthetic_entries
from backend.config import get_settings

router = APIRouter()
settings = get_settings()


def _dnt_endpoint() -> str:
    """When OTELA_FIXTURE_PATH is set, read DNT from disk instead of HTTP —
    used for iterating on the UI against synthesised post-upgrade payloads."""
    if settings.otela_fixture_path:
        return settings.otela_fixture_path
    return settings.otela_head_addr + "/v1/dnt/table"


async def _with_passthrough(models: list[dict], with_details: bool) -> list[dict]:
    """Append synthetic passthrough-provider entries (CSCS-Inference/...,
    RCP-AIaaS/...). Provider prefixes keep these ids disjoint from
    OpenTela-served ones, so a local launch and its passthrough twin are
    both listed. The id-collision skip below only fires if something
    launches locally under a reserved provider prefix (squatting): the
    local entry keeps the listing but resolve_model still routes the id
    to the provider, so don't name local launches after provider
    prefixes."""
    existing = {m["id"] for m in models if m.get("id")}
    for entry in await get_synthetic_entries(with_details=with_details):
        if entry["id"] not in existing:
            models.append(entry)
    return models


@router.get("/v1/models_detailed")
async def list_models_detailed():
    models = platform_namespaced(get_all_models(_dnt_endpoint(), with_details=True))
    models = await _with_passthrough(models, with_details=True)
    return dict(
        object="list",
        data=models,
    )


@router.get("/v1/models")
async def list_models():
    models = platform_namespaced(get_all_models(_dnt_endpoint(), with_details=False))
    models = await _with_passthrough(models, with_details=False)
    return dict(
        object="list",
        data=models,
    )
