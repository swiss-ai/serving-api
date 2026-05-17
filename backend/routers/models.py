from fastapi import APIRouter
from backend.services.model_service import get_all_models
from backend.config import get_settings

router = APIRouter()
settings = get_settings()


def _dnt_endpoint() -> str:
    """When OTELA_FIXTURE_PATH is set, read DNT from disk instead of HTTP —
    used for iterating on the UI against synthesised post-upgrade payloads."""
    if settings.otela_fixture_path:
        return settings.otela_fixture_path
    return settings.otela_head_addr + "/v1/dnt/table"


@router.get("/v1/models_detailed")
async def list_models_detailed():
    models = get_all_models(_dnt_endpoint(), with_details=True)
    return dict(
        object="list",
        data=models,
    )


@router.get("/v1/models")
async def list_models():
    models = get_all_models(_dnt_endpoint(), with_details=False)
    return dict(
        object="list",
        data=models,
    )
