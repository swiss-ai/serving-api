from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials
from backend.middleware.auth import optional_security
from backend.services.auth_service import get_email_for_token
from backend.services.authorization_service import (
    effective_authorization,
    grants_access,
    normalize_policy,
)
from backend.services.identity_service import display_names
from backend.services.model_access_service import PUBLIC, load_overrides, may_edit
from backend.services.model_service import get_all_models, platform_namespaced
from backend.services.monitoring_service import is_admin
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


class _Caller:
    """Who is asking, as far as the catalogue is concerned: an identity to
    filter by, and whether they are an admin (who may manage anyone's
    models, and so is offered the access menu on all of them)."""

    def __init__(self, email: str | None, admin: bool):
        self.email = email
        self.admin = admin


def _caller(
    request: Request, credentials: HTTPAuthorizationCredentials | None
) -> _Caller:
    """The bearer token is OPTIONAL here: no header → anonymous caller
    (sees public entries only). A header that IS present must resolve to a
    known API key, though — a typo'd key should surface as 401, not as a
    silently narrower model list.

    The admin lookup costs one primary-key read, and only for an
    authenticated caller: it decides whether each entry comes back marked
    manageable, which an admin's does for every model."""
    if credentials is None:
        return _Caller(None, False)
    engine = request.app.state.engine
    email = get_email_for_token(engine, credentials.credentials)
    if email is None:
        raise HTTPException(status_code=401, detail="Invalid access token")
    return _Caller(email, is_admin(engine, api_key=credentials.credentials))


def _visible_to(models: list[dict], email: str | None, overrides: dict) -> list[dict]:
    """Filter each entry by its OWN effective policy — the override its
    launch carries if there is one, else the ``authorization`` label it
    started with (pending and follower peers carry the same labels as their
    head). Synthetic passthrough entries have neither, so they read as
    public.

    Listing has to honour overrides for the same reason enforcement does:
    a model someone has just made private should stop appearing for
    everyone else, not merely start refusing them."""
    return [
        m
        for m in models
        if grants_access(effective_authorization(m.get("labels"), overrides), email)
    ]


# Labels that name a person by email address. The DNT is echoed to
# /v1/models* almost verbatim (``labels`` is a passthrough dict, and the
# frontend prints the unrecognised ones), so these have to be taken out of
# the response rather than merely left unread by our own UI.
_IDENTIFYING_LABELS = ("launched_by_email", "authorization")


def _presented(
    models: list[dict], caller: _Caller, overrides: dict, engine
) -> list[dict]:
    """Rewrite the entries the caller may see into what we are willing to
    publish: people named by ``launched_by_name`` rather than by address.

    /v1/models* is anonymously readable (the public model list has to render
    for a logged-out visitor), so every address SML stamps on a peer —
    the launcher's, and every collaborator on a restricted model — would
    otherwise be a harvestable field of a public document. Translating here
    rather than in the frontend is the whole point: a name rendered from a
    payload that still carries the address protects nobody, since the
    scraper reads the payload.

    ``authorization`` therefore ships as a name list, and is reported as
    the EFFECTIVE policy — the launch's override where it has one, else its
    label — so the "Restricted" badge tells the truth about a model whose
    access was changed after launch, instead of waiting for the card's
    access panel to correct it.

    Raw addresses remain available where they are needed and already
    access-controlled: /v1/model-access/<model> gives a launch's owner (or
    an admin) the real allowlist to edit, /v1/profile gives you your own.
    """
    effective = [
        (entry, effective_authorization(entry.get("labels"), overrides))
        for entry in models
    ]

    # One lookup for the whole response: every launcher, plus everyone named
    # by a policy we are about to render.
    wanted: set[str] = set()
    for entry, policy_value in effective:
        wanted.add((entry.get("labels") or {}).get("launched_by_email", ""))
        wanted.update(normalize_policy(policy_value) or ())
    names = display_names(engine, wanted)

    presented = []
    for entry, policy_value in effective:
        entry_labels = entry.get("labels") or {}
        owner_email = entry_labels.get("launched_by_email", "")
        policy = normalize_policy(policy_value)
        presented.append(
            {
                **{
                    key: value
                    for key, value in entry.items()
                    if key not in _IDENTIFYING_LABELS
                },
                "labels": {
                    key: value
                    for key, value in entry_labels.items()
                    if key not in _IDENTIFYING_LABELS
                },
                "launched_by_name": names.get(owner_email, ""),
                "authorization": (
                    PUBLIC
                    if policy is None
                    else ", ".join(
                        sorted(names.get(member, member) for member in policy)
                    )
                ),
                # Lets the card offer its access menu without being told who
                # the owner is — the comparison that used to need the owner's
                # address AND the viewer's happens here instead. Presentation
                # only: /v1/model-access re-checks on every read and write.
                "can_manage_access": may_edit(caller.email, owner_email, caller.admin),
            }
        )
    return presented


@router.get("/v1/models_detailed")
async def list_models_detailed(
    request: Request,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(optional_security)
    ] = None,
):
    caller = _caller(request, credentials)
    engine = request.app.state.engine
    overrides = load_overrides(engine)
    models = platform_namespaced(get_all_models(_dnt_endpoint(), with_details=True))
    models = await _with_passthrough(models, with_details=True)
    visible = _visible_to(models, caller.email, overrides)
    return dict(object="list", data=_presented(visible, caller, overrides, engine))


@router.get("/v1/models")
async def list_models(
    request: Request,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(optional_security)
    ] = None,
):
    caller = _caller(request, credentials)
    engine = request.app.state.engine
    overrides = load_overrides(engine)
    models = platform_namespaced(get_all_models(_dnt_endpoint(), with_details=False))
    models = await _with_passthrough(models, with_details=False)
    visible = _visible_to(models, caller.email, overrides)
    return dict(object="list", data=_presented(visible, caller, overrides, engine))
