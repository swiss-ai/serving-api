"""Where an inference request goes: the OpenTela network or a passthrough
provider (CSCS L1, EPFL RCP, ...).

Every inference router — chat, completions, responses, embeddings and the
pooling family (rerank, score, classify, tokenize, detokenize) — resolves
the requested model id through ``resolve_route`` so the namespace rules,
the provider key swap, the upstream id rewrite and the passthrough-only
rate limit apply the same way on every endpoint. Until this module
existed only chat/completions/responses consulted the registry: a
prefixed id such as ``RCP-AIaaS/Qwen/Qwen3-Embedding-8B`` went verbatim
to OpenTela from ``/v1/embeddings``, which has no service by that name and
answered 503 "No provider found for the requested service".

Two upstream base URLs exist because vLLM serves the pooling family at
the server root, not under ``/v1`` like chat/completions/embeddings:

- ``server_root=False`` → the OpenAI-style base (``.../v1``). Callers
  append ``/chat/completions``, ``/embeddings``, ...
- ``server_root=True`` → the server root. Callers append ``/rerank``,
  ``/score``, ``/classify``, ``/tokenize``, ``/detokenize``.

On the OpenTela arm those are ``<head>/v1/service/llm/v1/`` and
``<head>/v1/service/llm/``; on the passthrough arm the provider's base
URL with and without its trailing ``/v1``.
"""

from dataclasses import dataclass

from backend.config import get_settings
from backend.middleware.ratelimit import enforce_rate_limit
from backend.services.passthrough_service import (
    ResolvedModel,
    endpoint as passthrough_endpoint,
    resolve_model,
    root_endpoint as passthrough_root_endpoint,
)


@dataclass(frozen=True)
class Route:
    """The outcome of routing one request.

    ``endpoint`` is the upstream base to append the operation path to,
    ``api_key`` the bearer to forward (the provider's shared key on the
    passthrough arm, the caller's own token on OpenTela), and
    ``provider_label`` the provider's display name — recorded as the perf
    "served on" dimension — or None for OpenTela. ``resolved`` is the
    namespace resolution (None for ids that fall through to OpenTela
    under an unknown namespace): callers forward ``upstream_id`` and
    surface ``public_id`` in responses.
    """

    endpoint: str
    api_key: str
    provider_label: str | None
    resolved: ResolvedModel | None

    @property
    def is_passthrough(self) -> bool:
        return self.resolved is not None and self.resolved.provider is not None

    def upstream_model(self, requested: str) -> str:
        """The id the serving side knows the model as."""
        return self.resolved.upstream_id if self.resolved is not None else requested

    def restore_public_id(self, payload):
        """Rewrite the ``model`` field of an upstream response body back
        to the prefixed id the client asked for. No-op for anything that
        isn't a dict carrying ``model`` or when nothing was resolved."""
        if (
            self.resolved is not None
            and isinstance(payload, dict)
            and "model" in payload
        ):
            payload["model"] = self.resolved.public_id
        return payload


async def resolve_route(
    model: str, user_token: str, *, server_root: bool = False
) -> Route:
    """Prefixed passthrough ids (CSCS-Inference/..., RCP-AIaaS/...) go to
    that provider's upstream endpoint with its shared key; SwissAI-Research/
    ids (this platform's own namespace) and bare ids stay on the OpenTela
    proxy with the user's bearer token forwarded as-is.

    Rate limiting happens here, only on the passthrough arm: external
    providers are a shared, platform-accountable resource (shared API
    key, external quota), while OpenTela models (bare or under
    SwissAI-Research/) run on the user's own GPU allocation and stay
    unlimited."""
    resolved = await resolve_model(model)
    if resolved is not None and resolved.provider is not None:
        enforce_rate_limit(user_token)
        provider = resolved.provider
        base = (
            passthrough_root_endpoint(provider)
            if server_root
            else passthrough_endpoint(provider)
        )
        return Route(base, provider.api_key, provider.device, resolved)
    head = get_settings().otela_head_addr
    base = head + ("/v1/service/llm/" if server_root else "/v1/service/llm/v1/")
    return Route(base, user_token, None, resolved)
