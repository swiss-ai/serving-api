from typing import Annotated
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from backend.services.auth_service import verify_token

security = HTTPBearer()
optional_security = HTTPBearer(auto_error=False)


async def require_auth(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
) -> str:
    engine = request.app.state.engine
    if not verify_token(engine, credentials.credentials):
        raise HTTPException(status_code=401, detail="Invalid access token")
    return credentials.credentials
