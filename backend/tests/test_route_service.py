"""Routing every inference endpoint through the passthrough registry.

Regression for the RCP embeddings report: ``RCP-AIaaS/Qwen/Qwen3-Embedding-8B``
was listed in /v1/models but /v1/embeddings forwarded the prefixed id
verbatim to OpenTela, which answered 503 "No provider found for the
requested service". Only chat/completions/responses consulted the registry.
Now embeddings and the whole pooling family (rerank, score, classify,
tokenize, detokenize) resolve through ``route_service.resolve_route``: the
request goes to the provider with its key and un-prefixed id, the response
carries the public id again, and the passthrough rate limit applies."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.middleware.auth import require_auth
from backend.models.protocols import ModelResponse
from backend.services.llm_service import RawResponse
from backend.routers import classify, embeddings, rerank, responses, tokenization
from backend.services import route_service
from backend.services.passthrough_service import (
    Provider,
    ResolvedModel,
    root_endpoint,
)

RCP = Provider(
    name="rcp",
    base_url="https://rcp.example/v1",
    api_key="provider-key",
    device="EPFL RCP",
    prefix="RCP-AIaaS",
)
EMBED_PUBLIC = "RCP-AIaaS/Qwen/Qwen3-Embedding-8B"
EMBED_UPSTREAM = "Qwen/Qwen3-Embedding-8B"
RCP_RESOLVED = ResolvedModel(
    provider=RCP, upstream_id=EMBED_UPSTREAM, public_id=EMBED_PUBLIC
)
PLATFORM_RESOLVED = ResolvedModel(
    provider=None,
    upstream_id="SwissAI-Research/org/model",
    public_id="SwissAI-Research/org/model",
)


class _Settings:
    otela_head_addr = "http://otela"


@pytest.fixture(autouse=True)
def _no_rate_limit():
    """Rate limiting has its own tests; here it must simply not block."""
    with (
        patch.object(route_service, "enforce_rate_limit") as limiter,
        patch.object(route_service, "get_settings", return_value=_Settings()),
    ):
        yield limiter


def _resolve(resolved):
    return patch.object(
        route_service, "resolve_model", new=AsyncMock(return_value=resolved)
    )


# ── resolve_route ────────────────────────────────────────────────────────────


def test_passthrough_route_uses_provider_base_and_key(_no_rate_limit):
    with _resolve(RCP_RESOLVED):
        route = asyncio.run(route_service.resolve_route(EMBED_PUBLIC, "user-tok"))
    assert route.endpoint == "https://rcp.example/v1"
    assert route.api_key == "provider-key"
    assert route.provider_label == "EPFL RCP"
    assert route.is_passthrough
    assert route.upstream_model(EMBED_PUBLIC) == EMBED_UPSTREAM
    _no_rate_limit.assert_called_once_with("user-tok")


def test_passthrough_route_server_root_strips_v1(_no_rate_limit):
    with _resolve(RCP_RESOLVED):
        route = asyncio.run(
            route_service.resolve_route(EMBED_PUBLIC, "user-tok", server_root=True)
        )
    assert route.endpoint == "https://rcp.example"


def test_opentela_route_keeps_user_token(_no_rate_limit):
    with _resolve(None):
        openai = asyncio.run(route_service.resolve_route("user/org/m", "user-tok"))
        root = asyncio.run(
            route_service.resolve_route("user/org/m", "user-tok", server_root=True)
        )
    assert openai.endpoint == "http://otela/v1/service/llm/v1/"
    assert root.endpoint == "http://otela/v1/service/llm/"
    assert openai.api_key == root.api_key == "user-tok"
    assert openai.provider_label is None
    assert not openai.is_passthrough
    assert openai.upstream_model("user/org/m") == "user/org/m"
    _no_rate_limit.assert_not_called()


def test_platform_namespace_forwards_full_id(_no_rate_limit):
    with _resolve(PLATFORM_RESOLVED):
        route = asyncio.run(
            route_service.resolve_route("SwissAI-Research/org/model", "tok")
        )
    assert route.endpoint.startswith("http://otela/")
    assert route.api_key == "tok"
    assert route.upstream_model("SwissAI-Research/org/model") == (
        "SwissAI-Research/org/model"
    )
    _no_rate_limit.assert_not_called()


def test_restore_public_id_only_touches_dict_with_model():
    with _resolve(RCP_RESOLVED):
        route = asyncio.run(route_service.resolve_route(EMBED_PUBLIC, "tok"))
    assert route.restore_public_id({"model": EMBED_UPSTREAM, "x": 1}) == {
        "model": EMBED_PUBLIC,
        "x": 1,
    }
    assert route.restore_public_id({"tokens": [1]}) == {"tokens": [1]}
    assert route.restore_public_id([1, 2]) == [1, 2]


@pytest.mark.parametrize(
    "base_url, expected",
    [
        ("https://rcp.example/v1", "https://rcp.example"),
        ("https://rcp.example/v1/", "https://rcp.example"),
        ("https://gateway.example/llm", "https://gateway.example/llm"),
    ],
)
def test_root_endpoint(base_url, expected):
    provider = Provider(name="p", base_url=base_url, api_key="k", device="d")
    assert root_endpoint(provider) == expected


# ── routers ──────────────────────────────────────────────────────────────────


def _client(module) -> TestClient:
    app = FastAPI()
    app.include_router(module.router)
    app.dependency_overrides[require_auth] = lambda: "user-tok"
    return TestClient(app, raise_server_exceptions=False)


def test_embeddings_routes_prefixed_id_to_provider():
    """The reported failure: an RCP embedding model must reach RCP with
    the provider key and un-prefixed id, and come back under its public
    id."""
    proxy = AsyncMock(
        return_value=ModelResponse(
            object="list", model=EMBED_UPSTREAM, data=[], choices=[]
        )
    )
    with (
        _resolve(RCP_RESOLVED),
        patch.object(embeddings, "llm_proxy_embeddings", proxy),
    ):
        resp = _client(embeddings).post(
            "/v1/embeddings", json={"model": EMBED_PUBLIC, "input": "hello"}
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["model"] == EMBED_PUBLIC
    kwargs = proxy.await_args.kwargs
    assert kwargs["endpoint"] == "https://rcp.example/v1"
    assert kwargs["api_key"] == "provider-key"
    assert kwargs["provider_label"] == "EPFL RCP"
    assert kwargs["model"] == EMBED_UPSTREAM
    assert kwargs["input"] == "hello"


def test_embeddings_opentela_arm_unchanged():
    proxy = AsyncMock(
        return_value=ModelResponse(object="list", model="u/org/m", data=[], choices=[])
    )
    with _resolve(None), patch.object(embeddings, "llm_proxy_embeddings", proxy):
        resp = _client(embeddings).post(
            "/v1/embeddings", json={"model": "u/org/m", "input": "hello"}
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["model"] == "u/org/m"
    kwargs = proxy.await_args.kwargs
    assert kwargs["endpoint"] == "http://otela/v1/service/llm/v1/"
    assert kwargs["api_key"] == "user-tok"
    assert kwargs["provider_label"] is None
    assert kwargs["model"] == "u/org/m"


POOLING_ROUTES = [
    (rerank, "llm_proxy_rerank", "/v1/rerank", {"query": "q", "documents": ["a"]}),
    (rerank, "llm_proxy_score", "/v1/score", {"text_1": "a", "text_2": "b"}),
    (classify, "llm_proxy_classify", "/v1/classify", {"input": "a"}),
    (tokenization, "llm_proxy_tokenize", "/v1/tokenize", {"prompt": "a"}),
    (tokenization, "llm_proxy_detokenize", "/v1/detokenize", {"tokens": [1]}),
]


@pytest.mark.parametrize("module, proxy_name, path, body", POOLING_ROUTES)
def test_pooling_routes_prefixed_id_to_provider_root(module, proxy_name, path, body):
    """The pooling family lives at the vLLM server root: on the provider
    arm the base is the provider URL minus /v1, with key + id swapped."""
    proxy = AsyncMock(
        return_value=RawResponse(data={"model": EMBED_UPSTREAM, "ok": True})
    )
    with _resolve(RCP_RESOLVED), patch.object(module, proxy_name, proxy):
        resp = _client(module).post(path, json={"model": EMBED_PUBLIC, **body})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"model": EMBED_PUBLIC, "ok": True}
    kwargs = proxy.await_args.kwargs
    assert kwargs["endpoint"] == "https://rcp.example"
    assert kwargs["api_key"] == "provider-key"
    assert kwargs["provider_label"] == "EPFL RCP"
    assert kwargs["model"] == EMBED_UPSTREAM
    assert kwargs["payload"]["model"] == EMBED_UPSTREAM
    for key, value in body.items():
        assert kwargs["payload"][key] == value


@pytest.mark.parametrize("module, proxy_name, path, body", POOLING_ROUTES)
def test_pooling_routes_opentela_arm_unchanged(module, proxy_name, path, body):
    proxy = AsyncMock(return_value=RawResponse(data={"model": "u/org/m", "ok": True}))
    with _resolve(None), patch.object(module, proxy_name, proxy):
        resp = _client(module).post(path, json={"model": "u/org/m", **body})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"model": "u/org/m", "ok": True}
    kwargs = proxy.await_args.kwargs
    assert kwargs["endpoint"] == "http://otela/v1/service/llm/"
    assert kwargs["api_key"] == "user-tok"
    assert kwargs["payload"]["model"] == "u/org/m"


def test_responses_routes_prefixed_id_to_provider():
    proxy = AsyncMock(
        return_value=RawResponse(data={"model": EMBED_UPSTREAM, "output": []})
    )
    with _resolve(RCP_RESOLVED), patch.object(responses, "llm_proxy_responses", proxy):
        resp = _client(responses).post(
            "/v1/responses", json={"model": EMBED_PUBLIC, "input": "hi"}
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["model"] == EMBED_PUBLIC
    kwargs = proxy.await_args.kwargs
    assert kwargs["endpoint"] == "https://rcp.example/v1"
    assert kwargs["api_key"] == "provider-key"
    assert kwargs["payload"]["model"] == EMBED_UPSTREAM
