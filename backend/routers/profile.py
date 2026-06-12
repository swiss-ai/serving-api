from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from backend.services.auth_service import (
    get_profile_from_accesstoken,
    get_or_create_apikey,
    rotate_key_by_email,
)

router = APIRouter()
security = HTTPBearer()


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
        return HTTPException(
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
