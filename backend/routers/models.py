from fastapi import APIRouter
from backend.services.model_service import get_all_models
from backend.config import get_settings

router = APIRouter()
settings = get_settings()


@router.get("/v1/models_detailed")
async def list_models_detailed():
    models = get_all_models(settings.ocf_head_addr + "/v1/dnt/table", with_details=True)
    return dict(
        object="list",
        data=models,
    )


@router.get("/v1/models")
async def list_models():
    models = get_all_models(
        settings.ocf_head_addr + "/v1/dnt/table", with_details=False
    )
    return dict(
        object="list",
        data=models,
    )
