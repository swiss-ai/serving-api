from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from backend.services.auth_service import get_profile_from_accesstoken, get_or_create_apikey

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
