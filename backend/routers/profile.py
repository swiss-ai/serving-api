from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from backend.services.auth_service import (
    get_profile_from_accesstoken,
    get_or_create_apikey,
    rotate_key_by_email,
)
from backend.services.monitoring_service import (
    TTL_CHOICES,
    LEVEL_RANK,
    delete_rule,
    get_effective_level,
    get_rules_for,
    upsert_rule,
)

router = APIRouter()
security = HTTPBearer()


def _email_from_credentials(credentials) -> str:
    try:
        return get_profile_from_accesstoken(credentials.credentials)["email"]
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid access token")


@router.get("/v1/profile")
async def get_profile(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)] = None,
):
    try:
        user_profile = get_profile_from_accesstoken(credentials.credentials)
        if user_profile:
            engine = request.app.state.engine
            api_key = get_or_create_apikey(engine, user_profile["email"])
        user_profile["api_key"] = api_key.key
        user_profile["budget"] = api_key.budget
        return user_profile
    except Exception:
        raise HTTPException(
            status_code=401,
            detail="Invalid access token",
        )


@router.post("/v1/profile/rotate")
async def rotate_profile_key(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)] = None,
):
    """Reset the caller's API key.

    Authenticates with the Auth0 access token (not the API key itself), looks
    up the user's key by email, generates a fresh one, and invalidates the old
    key in the Redis token cache. The new key is cached lazily on its first
    request, so no full cache flush is needed.
    """
    try:
        user_profile = get_profile_from_accesstoken(credentials.credentials)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid access token")

    engine = request.app.state.engine
    try:
        api_key = rotate_key_by_email(engine, user_profile["email"])
    except ValueError:
        raise HTTPException(status_code=404, detail="No API key found for this user")

    user_profile["api_key"] = api_key.key
    user_profile["budget"] = api_key.budget
    return user_profile


class SelfMonitoringIn(BaseModel):
    level: str  # 'metadata' | 'full'
    ttl: str  # '1h' | '6h' | '1d' | '7d' | '90d'


@router.get("/v1/profile/monitoring")
async def get_own_monitoring(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)] = None,
):
    """The caller's own monitoring state: their opt-in rule (if any) and the
    effective level currently applied to their traffic (which may also come
    from an admin rule)."""
    email = _email_from_credentials(credentials)
    engine = request.app.state.engine
    self_rules = [
        r
        for r in get_rules_for(engine, email, active_only=False)
        if r["source"] == "self"
    ]
    return {
        "self_rule": self_rules[0] if self_rules else None,
        "effective_level": get_effective_level(engine, email),
        "levels": sorted(LEVEL_RANK),
        "ttls": sorted(TTL_CHOICES),
    }


@router.put("/v1/profile/monitoring")
async def set_own_monitoring(
    request: Request,
    body: SelfMonitoringIn,
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)] = None,
):
    """Opt in to recording of your own requests until the TTL lapses."""
    email = _email_from_credentials(credentials)
    try:
        rule = upsert_rule(
            request.app.state.engine,
            owner_email=email,
            level=body.level,
            source="self",
            ttl=body.ttl,
            created_by=email,
            note="self opt-in",
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return rule.model_dump()


@router.delete("/v1/profile/monitoring")
async def delete_own_monitoring(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)] = None,
):
    """Withdraw your opt-in. Does not affect any admin-created rule."""
    email = _email_from_credentials(credentials)
    removed = delete_rule(request.app.state.engine, email, source="self")
    return {"removed": removed}
