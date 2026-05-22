import aiohttp

NAMESPACE = "rob-poc"
MCP_PORT = 8080

# (owner, repo) pairs mirroring ../rob-poc/tool-gym/dev/{deployment,service}.yaml
_MCP_TOOLS = [
    ("alan5543", "calculator-mcp"),
    ("metehangzl", "pokemcp"),
    ("ilaydabayrak", "simple-mcp"),
    ("harunguclu", "bible-mcp"),
    ("hcatakli", "dictionary-mcp"),
    ("ceydasimsekk", "book-mcp"),
    ("mach123089", "basic-float-math-mcp"),
    ("biocontext", "pubchem-mcp"),
    ("sellisd", "mcp-units"),
    ("daheepk", "arxiv-paper-mcp"),
    ("ahmetcvlk", "nationality-mcp"),
    ("yokingma", "time-mcp"),
    ("forceconstant", "lyrical-mcp"),
    ("mikechao", "metmuseum-mcp"),
    ("myrve", "mcp-fruit"),
    ("esmakymkci", "yeni-detect-lang-mcp"),
    ("hashicorp", "terraform-mcp-server"),
]

MCP_SERVERS = {
    f"{owner}/{repo}": f"http://tool-gym-{owner}-{repo}-dev.{NAMESPACE}.svc.cluster.local:{MCP_PORT}"
    for owner, repo in _MCP_TOOLS
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
    return [{"owner": k.split("/")[0], "repo": k.split("/")[1]} for k in MCP_SERVERS]
