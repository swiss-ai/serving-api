PUBLIC_DOMAIN = "swissai.svc.cscs.ch"
MCP_PATH = "/mcp"

# (owner, repo) pairs mirroring ../rob-poc/tool-gym/dev/{deployment,service,ingress}.yaml
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


def _public_url(owner: str, repo: str) -> str:
    return f"https://tool-gym-{owner}-{repo}-dev.{PUBLIC_DOMAIN}{MCP_PATH}"


def list_mcp_servers() -> list[dict]:
    return [
        {"owner": owner, "repo": repo, "url": _public_url(owner, repo)}
        for owner, repo in _MCP_TOOLS
    ]
