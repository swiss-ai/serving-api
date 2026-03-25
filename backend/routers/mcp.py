import aiohttp
from fastapi import APIRouter, Request, Depends
from fastapi.responses import Response, JSONResponse
from backend.middleware.auth import require_auth
from backend.services.mcp_service import mcp_proxy, list_mcp_servers

router = APIRouter()


@router.get("/v1/mcp")
async def get_mcp_servers(token: str = Depends(require_auth)):
    return {"servers": list_mcp_servers()}


@router.post("/v1/mcp/{owner}/{repo}")
async def mcp_endpoint(
    owner: str,
    repo: str,
    request: Request,
    token: str = Depends(require_auth),
):
    body = await request.body()
    try:
        data, status = await mcp_proxy(owner, repo, body)
        return Response(
            content=data,
            status_code=status,
            media_type="application/json",
        )
    except aiohttp.ClientError:
        return JSONResponse(
            status_code=404,
            content={"error": f"MCP server '{owner}/{repo}' not reachable"},
        )
