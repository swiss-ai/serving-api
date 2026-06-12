from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from backend.services.auth_service import (
    get_profile_from_accesstoken,
    get_or_create_apikey,
    rotate_key,
)

router = APIRouter()
security = HTTPBearer()


@router.post("/v1/rotate-key")
async def rotate_api_key(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)] = None,
):
    try:
        engine = request.app.state.engine
        new_key = rotate_key(engine, credentials.credentials)
        return {
            "api_key": new_key.key,
            "budget": new_key.budget,
        }
    except ValueError as e:
        return HTTPException(
            status_code=403,
            detail=str(e),
        )
    except Exception:
        return HTTPException(
            status_code=401,
            detail="Invalid access token",
        )


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
