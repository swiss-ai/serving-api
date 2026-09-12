"""Read and change a launched model's access policy after it is running.

`--authorization` at launch time sets a model's audience, but an OpenTela
label cannot be edited once the peer is up — so without this the only way
to change who may use a model is to take it down and relaunch it. These
endpoints put an override row in front of the label instead; DELETE removes
it and the label decides again ("Reset").

Authenticated like the admin endpoints: the bearer may be the caller's
serving API key (curl-friendly) or their IdP access token (what the
frontend holds). Editing is the launch owner's right, or any admin's.
"""

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from backend.config import get_settings
from backend.services.auth_service import get_profile_from_accesstoken
from backend.services.model_access_service import (
    InvalidPolicyError,
    PUBLIC,
    clear_overrides,
    load_overrides,
    may_edit,
    normalize_policy_value,
    set_overrides,
)
from backend.services.authorization_service import grants_access, normalize_policy
from backend.services.identity_service import display_names
from backend.services.model_service import get_all_models
from backend.services.monitoring_service import is_admin, resolve_owner_email

router = APIRouter()
security = HTTPBearer()
settings = get_settings()


class AccessUpdate(BaseModel):
    """`authorization` uses the same grammar as the launch flag: "public",
    or a comma-separated list of emails. "private" is not accepted — it is
    an SML-side shorthand for "the launcher", and by the time a model is
    running the concrete email is what gets stored."""

    authorization: str


class Caller:
    def __init__(self, email: Optional[str], admin: bool):
        self.email = email
        self.admin = admin


async def _require_caller(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
) -> Caller:
    """Resolve the bearer to an identity, by either accepted token type.

    Identity alone — the per-model permission check happens once we know
    which launch is being addressed, since it depends on who owns it."""
    engine = request.app.state.engine
    token = credentials.credentials

    email = resolve_owner_email(engine, token)
    if email is not None:
        return Caller(email, is_admin(engine, api_key=token))

    try:
        email = get_profile_from_accesstoken(token).get("email")
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid access token")
    if not email:
        raise HTTPException(status_code=401, detail="Invalid access token")
    return Caller(email, is_admin(engine, email=email))


def _dnt_endpoint() -> str:
    if settings.otela_fixture_path:
        return settings.otela_fixture_path
    return settings.otela_head_addr + "/v1/dnt/table"


def _launches_of(model_id: str) -> list[dict]:
    """Every distinct launch currently serving ``model_id``.

    Read live rather than from the authorization cache: this is the
    management path, where showing a policy that is ten seconds out of date
    right after someone saved would be worse than the extra fetch.

    One record per launch_id, not per peer — a model's replicas and
    followers share their head's labels, so collapsing them is what makes
    "this model" a single thing to edit.

    Deliberately NOT run through ``platform_namespaced``. That filter is
    listing-only: an entry it hides (an old OpenTela, an SML below the
    minimum) is still *routable*, so the gateway still enforces its label.
    Managing access off the filtered list would let us write an override to
    some of a name's launches and not others, which is precisely the
    disagreement that makes a name unroutable for everyone — the failure
    this endpoint's all-launches rule exists to prevent.
    """
    models = get_all_models(_dnt_endpoint(), with_details=True)
    launches: dict[str, dict] = {}
    for entry in models:
        if entry.get("id") != model_id:
            continue
        labels = entry.get("labels") or {}
        launch_id = labels.get("launch_id", "")
        launches.setdefault(
            launch_id,
            {
                "launch_id": launch_id,
                "owner_email": labels.get("launched_by_email", ""),
                "launched_by": labels.get("launched_by", ""),
                "label_authorization": labels.get("authorization", "") or PUBLIC,
            },
        )
    return list(launches.values())


def _state(request: Request, model_id: str, caller: Caller) -> dict:
    """The access state of one model, from a manager's point of view.

    ``effective_authorization`` is what the gateway actually enforces;
    ``label_authorization`` is what the model was launched with, which is
    what Reset returns it to. Keeping both on the wire is what lets the UI
    say "changed from X" and offer Reset only when there is something to
    reset.
    """
    launches = _launches_of(model_id)
    if not launches:
        raise HTTPException(
            status_code=404, detail=f"No running model named '{model_id}'."
        )

    overrides = load_overrides(request.app.state.engine)
    for launch in launches:
        launch_id = launch["launch_id"]
        override = overrides.get(launch_id) if launch_id else None
        launch["override_policy"] = override
        launch["effective_authorization"] = override or launch["label_authorization"]

    effective = {normalize_policy(le["effective_authorization"]) for le in launches}
    labels = {normalize_policy(le["label_authorization"]) for le in launches}

    return {
        "model_id": model_id,
        # A name served by launches that disagree is refused for everyone
        # (ADR-0001), so report the disagreement rather than picking one.
        "conflict": len(effective) > 1,
        "effective_authorization": (
            launches[0]["effective_authorization"] if len(effective) == 1 else None
        ),
        "label_authorization": (
            launches[0]["label_authorization"] if len(labels) == 1 else None
        ),
        "is_overridden": any(le["override_policy"] for le in launches),
        "can_edit": all(
            may_edit(caller.email, le["owner_email"], caller.admin) for le in launches
        ),
        "editable": all(bool(le["launch_id"]) for le in launches),
        "owner_email": launches[0]["owner_email"],
        "launches": launches,
    }


def _presented(state: dict, engine) -> dict:
    """The access state as we are willing to return it to this caller.

    Someone who may EDIT gets the raw addresses: they are the allowlist
    being edited, and there is no way to hand back a list to change without
    them.

    Everyone else gets what the public catalogue gets — people named, never
    addressed. Without this, `_require_visible` admits anyone the model is
    visible to, which for a PUBLIC model is every authenticated caller; a
    `GET` per id off /v1/models would then reassemble the launcher-address
    harvest that stripping the labels was meant to stop (see
    :mod:`backend.services.identity_service`), and on a restricted model
    would hand a listed collaborator everyone else's address too.
    """
    if state["can_edit"]:
        return state

    launches = state["launches"]
    wanted = {state["owner_email"], *(le["owner_email"] for le in launches)}
    for policy_value in (
        state["effective_authorization"],
        state["label_authorization"],
        *(le["label_authorization"] for le in launches),
        *(le["effective_authorization"] for le in launches),
        *(le["override_policy"] for le in launches),
    ):
        wanted.update(normalize_policy(policy_value or "") or ())
    names = display_names(engine, {email for email in wanted if email})

    def as_names(policy_value: str | None) -> str | None:
        """One policy rendered as people. None stays None — it is how
        ``_state`` reports a disagreement, not a policy."""
        if policy_value is None:
            return None
        policy = normalize_policy(policy_value)
        if policy is None:
            return PUBLIC
        return ", ".join(sorted(names.get(member, member) for member in policy))

    return {
        **{key: value for key, value in state.items() if key != "owner_email"},
        "owner_name": names.get(state["owner_email"], ""),
        "effective_authorization": as_names(state["effective_authorization"]),
        "label_authorization": as_names(state["label_authorization"]),
        "launches": [
            {
                **{k: v for k, v in le.items() if k != "owner_email"},
                "owner_name": names.get(le["owner_email"], ""),
                "label_authorization": as_names(le["label_authorization"]),
                "effective_authorization": as_names(le["effective_authorization"]),
                "override_policy": as_names(le["override_policy"]),
            }
            for le in launches
        ],
    }


def _require_visible(state: dict, caller: Caller) -> None:
    """A model's access settings name the people who may use it, so they are
    only shown to someone already on that list (or an admin). Otherwise
    anyone could read a restricted model's collaborator list off this
    endpoint — exactly the disclosure the label filtering prevents on
    /v1/models."""
    if caller.admin or state["can_edit"]:
        return
    if state["conflict"] or not grants_access(
        state["effective_authorization"] or "", caller.email
    ):
        raise HTTPException(
            status_code=404,
            detail=f"No running model named '{state['model_id']}'.",
        )


def _require_editable(state: dict, caller: Caller) -> None:
    """Everything that must hold before we write an override."""
    # Answer a caller who cannot even see the model exactly as GET does.
    # Otherwise a 403 here would confirm the model exists to someone the
    # listing deliberately hides it from.
    _require_visible(state, caller)
    if not state["can_edit"]:
        # Report the launch that actually blocks the edit. With several
        # launches under one name, "you don't own it" and "it has no owner"
        # can both be true of different ones, and naming the wrong one sends
        # the user looking in the wrong place.
        ownerless = any(not le["owner_email"] for le in state["launches"])
        raise HTTPException(
            status_code=403,
            detail=(
                "This model was launched without an owner label, so only an "
                "admin can change its access."
                if ownerless
                else "Only the user who launched this model, or an admin, can "
                "change its access."
            ),
        )
    if not state["editable"]:
        raise HTTPException(
            status_code=409,
            detail=(
                "This model was launched by a version of SML that does not stamp a "
                "launch id, so its access cannot be changed after the fact. "
                "Relaunch it with a current SML, or set --authorization at launch."
            ),
        )


@router.get("/v1/model-access/{model_id:path}")
async def get_model_access(
    request: Request,
    model_id: str,
    caller: Annotated[Caller, Depends(_require_caller)],
):
    """What this model's access is now, what it launched as, and whether the
    caller may change it."""
    state = _state(request, model_id, caller)
    _require_visible(state, caller)
    return _presented(state, request.app.state.engine)


@router.put("/v1/model-access/{model_id:path}")
async def set_model_access(
    request: Request,
    model_id: str,
    body: AccessUpdate,
    caller: Annotated[Caller, Depends(_require_caller)],
):
    """Change who may use this model, from now until it is reset or the job
    ends.

    Applied to EVERY launch currently serving the name, not just one. Two
    launches under one name with different policies make it unroutable for
    everyone (ADR-0001), so a partial write would be a way to break your own
    model; requiring the caller to own all of them keeps that impossible.
    """
    state = _state(request, model_id, caller)
    _require_editable(state, caller)

    try:
        policy = normalize_policy_value(body.authorization)
    except InvalidPolicyError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    set_overrides(
        request.app.state.engine,
        [{**launch, "model_id": model_id} for launch in state["launches"]],
        policy,
        caller.email or "",
    )
    return _presented(_state(request, model_id, caller), request.app.state.engine)


@router.delete("/v1/model-access/{model_id:path}")
async def reset_model_access(
    request: Request,
    model_id: str,
    caller: Annotated[Caller, Depends(_require_caller)],
):
    """Reset: drop the override and let the model's launch-time
    ``authorization`` label decide again. A no-op if it was never
    overridden, so the button is safe to press twice."""
    state = _state(request, model_id, caller)
    _require_editable(state, caller)

    clear_overrides(
        request.app.state.engine,
        [launch["launch_id"] for launch in state["launches"]],
    )
    return _presented(_state(request, model_id, caller), request.app.state.engine)
