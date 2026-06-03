from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings


@lru_cache()
def get_settings():
    return Settings()


class Settings(BaseSettings):
    auth0_domain: str = ""
    auth0_api_audience: str = ""
    auth0_issuer: str = ""
    auth0_algorithms: str = "RS256"
    auth0_client_id: str = ""
    auth0_client_secret: str = ""
    database_url: str = ""
    auth_secret: str = ""
    auth_trust_host: bool = False
    # Accept the historical OCF_* env var names in addition to the canonical
    # OTELA_* ones so existing deployments keep working through the rename.
    # Python attribute access stays `settings.otela_*`.
    otela_head_addr: str = Field(
        default="",
        validation_alias=AliasChoices("otela_head_addr", "ocf_head_addr"),
    )
    # When set, /v1/models* reads this JSON file instead of calling
    # $otela_head_addr/v1/dnt/table. Used for UI iteration against synthesised
    # upgraded payloads (see backend/tests/fixtures/build_upgraded.py).
    otela_fixture_path: str = Field(
        default="",
        validation_alias=AliasChoices("otela_fixture_path", "ocf_fixture_path"),
    )
    # OpenAI-compatible passthrough providers — when a provider's pair is
    # set, chat/completion requests for model ids that provider exposes are
    # forwarded to its endpoint instead of the OpenTela network. Lets us
    # surface upstream-hosted models without launching our own k8s pods.
    # Each pair must be provided via env in k8s secrets; registration +
    # discovery live in backend/services/passthrough_service.py.
    #   CSCS L1: Apertus 8B/70B from the CSCS L1 service.
    cscs_l1_base_url: str = ""
    cscs_l1_api_key: str = ""
    #   EPFL RCP: another OpenAI-compatible upstream.
    rcp_base_url: str = ""
    rcp_api_key: str = ""
    langfuse_host: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    vite_auth0_client_id: str = ""
    vite_auth0_domain: str = ""
    firebase_service_account_json: str = ""
    access_log: bool = False

    class Config:
        env_file = ".env"
        populate_by_name = True


def parse_hardware_info(hardware_info):
    """
    Parse hardware information and return a string representation.

    Args:
        hardware_info (dict): Dictionary containing hardware information

    Returns:
        str: String representation of the hardware in the format "Nx[Spec]"
    """
    if not hardware_info or "gpus" not in hardware_info or not hardware_info["gpus"]:
        return "Unknown"
    # Group GPUs by name
    gpu_counts = {}
    for gpu in hardware_info["gpus"]:
        name = gpu.get("name", "Unknown GPU")
        gpu_counts[name] = gpu_counts.get(name, 0) + 1
    # Format the output
    result = []
    for gpu_name, count in gpu_counts.items():
        result.append(f"{count}x {gpu_name}")
    return ", ".join(result)
