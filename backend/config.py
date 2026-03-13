from pydantic_settings import BaseSettings
from functools import lru_cache


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
    logfire_token: str = ""
    database_url: str = ""
    auth_secret: str = ""
    auth_trust_host: bool = False
    ocf_head_addr: str = ""
    langfuse_host: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    vite_auth0_client_id: str = ""
    vite_auth0_domain: str = ""
    firebase_service_account_json: str = ""
    access_log: bool = False

    class Config:
        env_file = ".env"


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
