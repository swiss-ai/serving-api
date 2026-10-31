"""OpenAI-compatible /v1/audio/{transcriptions,translations} (issue #154).

Before these routes existed the gateway answered 404 for an ASR model that
was up and serving: no router matched. The request is multipart, so the
model id is read from the form; every other field and the file are
forwarded as received, with only ``model`` swapped for the upstream id,
and the upstream body comes back verbatim (``response_format`` may be
text, srt or vtt)."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.middleware.auth import require_auth
from backend.routers import audio
from backend.services import llm_service, route_service
from backend.services.llm_service import BinaryResponse, StreamWrapper
from backend.services.passthrough_service import Provider, ResolvedModel

RCP = Provider(
    name="rcp",
    base_url="https://rcp.example/v1",
    api_key="provider-key",
    device="EPFL RCP",
    prefix="RCP-AIaaS",
)
ASR_PUBLIC = "RCP-AIaaS/Qwen/Qwen3-ASR-1.7B"
ASR_UPSTREAM = "Qwen/Qwen3-ASR-1.7B"
RCP_RESOLVED = ResolvedModel(
    provider=RCP, upstream_id=ASR_UPSTREAM, public_id=ASR_PUBLIC
)
USER_MODEL = "ahadinia/Qwen/Qwen3-ASR-1.7B"
AUDIO = b"RIFF\x00\x00\x00\x00WAVEfmt "
OPERATIONS = ["transcriptions", "translations"]


class _Settings:
    otela_head_addr = "http://otela"


@pytest.fixture(autouse=True)
def _no_rate_limit():
    with (
        patch.object(route_service, "enforce_rate_limit"),
        patch.object(route_service, "get_settings", return_value=_Settings()),
    ):
        yield


def _resolve(resolved):
    return patch.object(
        route_service, "resolve_model", new=AsyncMock(return_value=resolved)
    )


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(audio.router)
    app.dependency_overrides[require_auth] = lambda: "user-tok"
    return TestClient(app, raise_server_exceptions=False)


def _post(operation, data, files=None):
    if files is None:
        files = {"file": ("clip.wav", AUDIO, "audio/wav")}
    return _client().post(f"/v1/audio/{operation}", data=data, files=files)


def _sent_fields(proxy) -> dict:
    """The forwarded multipart fields as {name: [(value, filename), ...]}."""
    fields = {}
    for options, _headers, value in proxy.await_args.kwargs["form"]._fields:
        fields.setdefault(options["name"], []).append((value, options.get("filename")))
    return fields


# ── router ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("operation", OPERATIONS)
def test_opentela_model_forwards_form(operation):
    """The reported case: a user launch on OpenTela, reached with the
    caller's own token at the OpenAI-style base."""
    proxy = AsyncMock(
        return_value=BinaryResponse(b'{"text":"Thus accomplished"}', "application/json")
    )
    with _resolve(None), patch.object(audio, "llm_proxy_audio", proxy):
        resp = _post(
            operation,
            {"model": USER_MODEL, "language": "en", "temperature": "0"},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"text": "Thus accomplished"}
    kwargs = proxy.await_args.kwargs
    assert kwargs["endpoint"] == "http://otela/v1/service/llm/v1/"
    assert kwargs["api_key"] == "user-tok"
    assert kwargs["operation"] == operation
    assert kwargs["stream"] is False
    fields = _sent_fields(proxy)
    assert fields["model"] == [(USER_MODEL, None)]
    assert fields["language"] == [("en", None)]
    assert fields["temperature"] == [("0", None)]
    assert fields["file"] == [(AUDIO, "clip.wav")]


def test_passthrough_model_swaps_key_and_id():
    proxy = AsyncMock(return_value=BinaryResponse(b'{"text":"x"}', "application/json"))
    with _resolve(RCP_RESOLVED), patch.object(audio, "llm_proxy_audio", proxy):
        resp = _post("transcriptions", {"model": ASR_PUBLIC})
    assert resp.status_code == 200, resp.text
    kwargs = proxy.await_args.kwargs
    assert kwargs["endpoint"] == "https://rcp.example/v1"
    assert kwargs["api_key"] == "provider-key"
    assert kwargs["provider_label"] == "EPFL RCP"
    assert kwargs["model"] == ASR_UPSTREAM
    assert _sent_fields(proxy)["model"] == [(ASR_UPSTREAM, None)]


def test_repeated_fields_survive():
    """timestamp_granularities[] is sent once per value."""
    proxy = AsyncMock(return_value=BinaryResponse(b"{}", "application/json"))
    with _resolve(None), patch.object(audio, "llm_proxy_audio", proxy):
        resp = _post(
            "transcriptions",
            {
                "model": USER_MODEL,
                "timestamp_granularities[]": ["word", "segment"],
            },
        )
    assert resp.status_code == 200, resp.text
    assert _sent_fields(proxy)["timestamp_granularities[]"] == [
        ("word", None),
        ("segment", None),
    ]


def test_non_json_body_returned_verbatim():
    srt = b"1\n00:00:00,000 --> 00:00:02,000\nThus accomplished\n"
    proxy = AsyncMock(return_value=BinaryResponse(srt, "text/plain; charset=utf-8"))
    with _resolve(None), patch.object(audio, "llm_proxy_audio", proxy):
        resp = _post("transcriptions", {"model": USER_MODEL, "response_format": "srt"})
    assert resp.status_code == 200, resp.text
    assert resp.content == srt
    assert resp.headers["content-type"].startswith("text/plain")


def test_stream_passes_sse_through():
    events = [b'data: {"delta":"Thus"}\n\n', b"data: [DONE]\n\n"]

    async def gen():
        for event in events:
            yield event

    proxy = AsyncMock(return_value=StreamWrapper(gen()))
    with _resolve(None), patch.object(audio, "llm_proxy_audio", proxy):
        resp = _post("transcriptions", {"model": USER_MODEL, "stream": "true"})
    assert resp.status_code == 200, resp.text
    assert proxy.await_args.kwargs["stream"] is True
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert resp.content == b"".join(events)


@pytest.mark.parametrize("operation", OPERATIONS)
def test_missing_file_is_400(operation):
    proxy = AsyncMock()
    with _resolve(None), patch.object(audio, "llm_proxy_audio", proxy):
        resp = _client().post(f"/v1/audio/{operation}", data={"model": USER_MODEL})
    assert resp.status_code == 400
    proxy.assert_not_awaited()


@pytest.mark.parametrize("model", [None, "Qwen3-ASR-1.7B"])
def test_unnamespaced_model_is_404(model):
    proxy = AsyncMock()
    data = {} if model is None else {"model": model}
    with _resolve(None), patch.object(audio, "llm_proxy_audio", proxy):
        resp = _post("transcriptions", data)
    assert resp.status_code == 404
    proxy.assert_not_awaited()


# ── llm_proxy_audio against a real upstream ──────────────────────────────────


@pytest.mark.parametrize("operation", OPERATIONS)
def test_proxy_sends_multipart_and_returns_body(operation):
    """The shared handler normally forces a JSON Content-Type; for audio
    aiohttp must set multipart with its boundary, and the reply is read as
    bytes rather than parsed."""
    seen = {}

    async def handler(request):
        seen["path"] = request.path
        seen["auth"] = request.headers["Authorization"]
        seen["content_type"] = request.content_type
        form = await request.post()
        seen["model"] = form["model"]
        seen["file"] = form["file"].file.read()
        return web.Response(text="Thus accomplished", content_type="text/plain")

    async def run():
        app = web.Application()
        app.router.add_post(f"/v1/audio/{operation}", handler)
        async with TestServer(app) as server:
            form = llm_service.aiohttp.FormData()
            form.add_field("model", ASR_UPSTREAM)
            form.add_field("file", AUDIO, filename="clip.wav", content_type="audio/wav")
            return await llm_service.llm_proxy_audio(
                endpoint=str(server.make_url("/v1/")),
                api_key="tok",
                operation=operation,
                form=form,
                stream=False,
                model=ASR_UPSTREAM,
            )

    with patch.object(llm_service.metrics_collector, "record"):
        resp = asyncio.run(run())
    assert isinstance(resp, BinaryResponse)
    assert resp.body == b"Thus accomplished"
    assert resp.media_type.startswith("text/plain")
    assert seen == {
        "path": f"/v1/audio/{operation}",
        "auth": "Bearer tok",
        "content_type": "multipart/form-data",
        "model": ASR_UPSTREAM,
        "file": AUDIO,
    }
