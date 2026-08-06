from fastapi import APIRouter
from backend.services.model_service import get_all_models
from backend.services.namespace_service import namespace_matches
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
    """Append synthetic passthrough-provider entries (CSCS L1, RCP, ...),
    skipping ids already present in the OpenTela result so we don't
    double-list a model that's still launched locally during a migration."""
    existing = {m["id"] for m in models if m.get("id")}
    for entry in await get_synthetic_entries(with_details=with_details):
        if entry["id"] not in existing:
            models.append(entry)
    return models


def _own_namespace_only(models: list[dict]) -> list[dict]:
    """Drop peers publishing a served name under someone else's username.

    A name like "alice/swiss-ai/X" coming from a job that ran as bob is a
    namespace squat; we don't advertise it, and ensure_namespace_ok refuses
    to route the id for anyone. Unnamespaced (pre-namespacing) ids and peers
    with no ``launched_by`` label are left alone — see namespace_matches."""
    return [
        m
        for m in models
        if namespace_matches(m.get("id", ""), m.get("launched_by", ""))
    ]


@router.get("/v1/models_detailed")
async def list_models_detailed():
    models = _own_namespace_only(get_all_models(_dnt_endpoint(), with_details=True))
    models = await _with_passthrough(models, with_details=True)
    return dict(
        object="list",
        data=models,
    )


@router.get("/v1/models")
async def list_models():
    models = _own_namespace_only(get_all_models(_dnt_endpoint(), with_details=False))
    models = await _with_passthrough(models, with_details=False)
    return dict(
        object="list",
        data=models,
    )
