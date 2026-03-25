import aiohttp

NAMESPACE = "rob-poc"
MCP_PORT = 8080

# Hardcoded MCP server registry: owner/repo -> K8s service DNS
MCP_SERVERS = {
    "alan5543/calculator-mcp": f"http://tool-gym-alan5543-calculator-mcp-dev.{NAMESPACE}.svc.cluster.local:{MCP_PORT}",
}


async def mcp_proxy(owner: str, repo: str, body: bytes) -> tuple[bytes, int]:
    """Forward a JSON-RPC request to the MCP server. Returns (body, status)."""
    url = MCP_SERVERS.get(f"{owner}/{repo}")
    if not url:
        return b'{"error":"MCP server not found"}', 404
    async with aiohttp.ClientSession() as session:
        async with session.post(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            data = await resp.read()
            return data, resp.status


def list_mcp_servers() -> list[dict]:
    return [
        {"owner": k.split("/")[0], "repo": k.split("/")[1]}
        for k in MCP_SERVERS
    ]
