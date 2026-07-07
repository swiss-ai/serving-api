from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings


@lru_cache()
def get_settings():
    return Settings()


class Settings(BaseSettings):
    # Selects the active IdP. Both credential sets can be present at once; this
    # flag decides which one is live, so a prod cutover (or rollback) is a
    # single env change with no image rebuild. See issue #58.
    auth_provider: str = "auth0"
    auth0_domain: str = ""
    auth0_issuer: str = ""
    auth0_client_id: str = ""
    auth0_client_secret: str = ""
    # Authentik credential set (dormant until auth_provider=authentik). When
    # unset these fall back to the auth0_* values so environments that already
    # hold Authentik values under the legacy AUTH0_* names keep working.
    authentik_issuer: str = ""
    authentik_client_id: str = ""
    authentik_client_secret: str = ""
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
    # LOCAL DEV ONLY. When true, the backend accepts the frontend's
    # `dev-dummy-token` (see frontend api_key.astro dev session) without
    # calling Auth0, so `make run` works without a real login. MUST stay
    # false in every deployed environment.
    dev_auth_bypass: bool = False

    def active_issuer(self) -> str:
        """Issuer for the currently selected provider. Falls back to the
        auth0_* value when the authentik_* set is unset so legacy
        environments keep working."""
        if self.auth_provider == "authentik":
            return self.authentik_issuer or self.auth0_issuer
        return self.auth0_issuer

    def fallback_issuer(self) -> str:
        """The *other* configured issuer, used to keep in-flight sessions
        valid across a provider flip/rollback. Empty when not configured."""
        if self.auth_provider == "authentik":
            return self.auth0_issuer
        return self.authentik_issuer

    def candidate_issuers(self) -> list[str]:
        """Distinct issuers to try when validating an access token: active
        first, then the other provider (if configured and different)."""
        issuers = [self.active_issuer()]
        other = self.fallback_issuer()
        if other and other not in issuers:
            issuers.append(other)
        return [i for i in issuers if i]

    class Config:
        env_file = ".env"
        populate_by_name = True
        # Deployments inject env vars that aren't modeled here (REDIS_*,
        # AUTH0_ALGORITHMS, AUTH0_API_AUDIENCE, ...). Ignore them instead of
        # crashing on startup — matches what the running prod image tolerates.
        extra = "ignore"


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
