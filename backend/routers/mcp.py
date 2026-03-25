import aiohttp
from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse, Response
from backend.middleware.auth import require_auth
from backend.services.mcp_service import list_mcp_servers, mcp_proxy

router = APIRouter()

EXAMPLE_TOOLS_LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
EXAMPLE_TOOL_CALL = {
    "jsonrpc": "2.0",
    "id": 2,
    "method": "tools/call",
    "params": {"name": "add", "arguments": {"a": 7, "b": 13}},
}


@router.get("/v1/mcp")
async def get_mcp_servers(token: str = Depends(require_auth)):
    return {"servers": list_mcp_servers()}


@router.post("/v1/mcp/{owner}/{repo}")
async def mcp_endpoint(
    owner: str,
    repo: str,
    payload: dict = Body(..., examples=[EXAMPLE_TOOLS_LIST, EXAMPLE_TOOL_CALL]),
    token: str = Depends(require_auth),
):
    import json

    body = json.dumps(payload).encode()
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
