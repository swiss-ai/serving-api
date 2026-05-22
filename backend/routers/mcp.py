from fastapi import APIRouter, Depends
from backend.middleware.auth import require_auth
from backend.services.mcp_service import list_mcp_servers

router = APIRouter()


@router.get("/v1/mcp")
async def get_mcp_servers(token: str = Depends(require_auth)):
    return {"servers": list_mcp_servers()}
