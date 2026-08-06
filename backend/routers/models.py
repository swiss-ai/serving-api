from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials
from backend.middleware.auth import optional_security
from backend.services.auth_service import get_email_for_token
from backend.services.authorization_service import grants_access, namespace_matches
from backend.services.model_service import get_all_models
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


def _caller_email(
    request: Request, credentials: HTTPAuthorizationCredentials | None
) -> str | None:
    """The bearer token is OPTIONAL here: no header → anonymous caller
    (sees public entries only). A header that IS present must resolve to a
    known API key, though — a typo'd key should surface as 401, not as a
    silently narrower model list."""
    if credentials is None:
        return None
    email = get_email_for_token(request.app.state.engine, credentials.credentials)
    if email is None:
        raise HTTPException(status_code=401, detail="Invalid access token")
    return email


def _visible_to(models: list[dict], email: str | None) -> list[dict]:
    """Filter each entry by its OWN ``authorization`` label (pending and
    follower peers carry the same labels as their head). Synthetic
    passthrough entries have no such label, so they read as public."""
    return [
        m
        for m in models
        if grants_access((m.get("labels") or {}).get("authorization", ""), email)
    ]


def _own_namespace_only(models: list[dict]) -> list[dict]:
    """Drop peers publishing a served name under someone else's username.

    A name like "alice/swiss-ai/X" coming from a job that ran as bob is a
    namespace squat; we don't advertise it, and ensure_model_access refuses
    to route the id for anyone. Unnamespaced (pre-namespacing) ids and peers
    with no ``launched_by`` label are left alone — see namespace_matches."""
    return [
        m
        for m in models
        if namespace_matches(m.get("id", ""), m.get("launched_by", ""))
    ]


@router.get("/v1/models_detailed")
async def list_models_detailed(
    request: Request,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(optional_security)
    ] = None,
):
    email = _caller_email(request, credentials)
    models = _own_namespace_only(get_all_models(_dnt_endpoint(), with_details=True))
    models = await _with_passthrough(models, with_details=True)
    return dict(
        object="list",
        data=_visible_to(models, email),
    )


@router.get("/v1/models")
async def list_models(
    request: Request,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(optional_security)
    ] = None,
):
    email = _caller_email(request, credentials)
    models = _own_namespace_only(get_all_models(_dnt_endpoint(), with_details=False))
    models = await _with_passthrough(models, with_details=False)
    return dict(
        object="list",
        data=_visible_to(models, email),
    )
