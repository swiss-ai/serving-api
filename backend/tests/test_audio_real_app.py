"""/v1/audio/* through the real app: real API key, real auth against
migrated Postgres, real routing and a real audio file. Only the model is
missing — a stub HTTP server stands in for it and records what arrived, so
these tests see the multipart body exactly as an upstream would.

test_audio.py covers the router's branches with the proxy mocked; this file
covers the path as a whole, which is what returned 404 in issue #154."""

import email
import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.services import passthrough_service, route_service
from backend.services.passthrough_service import Provider

AUDIO_DIR = Path(__file__).parent / "fixtures" / "audio"
WAV_NAME = "quick_16k_mono.wav"
WAV = (AUDIO_DIR / WAV_NAME).read_bytes()
# Each format a client is likely to upload, by file: see fixtures/audio/README.md.
AUDIO_FILES = [
    ("quick_8k_stereo.wav", "audio/wav"),
    ("quick_24k_mono.wav", "audio/wav"),
    ("quick_16k_mono.wav", "audio/wav"),
    ("quick_8k_mono.wav", "audio/wav"),
    ("quick_16k_mono.flac", "audio/flac"),
    ("fox_44k_stereo.mp3", "audio/mpeg"),
    ("fox_44k_stereo.ogg", "audio/ogg"),
]
UPSTREAM_MODEL = "openai/whisper-large-v3"
PUBLIC_MODEL = f"RCP-AIaaS/{UPSTREAM_MODEL}"
USER_MODEL = "someuser/openai/whisper-large-v3"
SRT = b"1\n00:00:00,000 --> 00:00:03,800\nThe quick brown fox\n"


class _Upstream:
    """Records each POST and answers with ``status``, ``body``, ``content_type``."""

    def __init__(self):
        self.requests = []
        self.status = 200
        self.body = SRT
        self.content_type = "text/plain; charset=utf-8"
        upstream = self

        class Handler(BaseHTTPRequestHandler):
            def _reply(self, status, body, content_type):
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                models = {"data": [{"id": UPSTREAM_MODEL}]}
                self._reply(200, json.dumps(models).encode(), "application/json")

            def do_POST(self):
                length = int(self.headers["Content-Length"])
                upstream.requests.append(
                    {
                        "path": self.path,
                        "authorization": self.headers["Authorization"],
                        "parts": _parts(
                            self.headers["Content-Type"], self.rfile.read(length)
                        ),
                    }
                )
                upstream._respond(self)

            def log_message(self, *args):
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_port}"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def _respond(self, handler):
        handler._reply(self.status, self.body, self.content_type)

    def close(self):
        self._server.shutdown()
        self._server.server_close()


def _parts(content_type: str, body: bytes) -> dict:
    """The multipart body as {name: part}, read the way a server reads it."""
    message = email.message_from_bytes(
        b"Content-Type: " + content_type.encode() + b"\r\n\r\n" + body
    )
    return {
        part.get_param("name", header="content-disposition"): part
        for part in message.get_payload()
    }


@pytest.fixture()
def upstream():
    server = _Upstream()
    yield server
    server.close()


@pytest.fixture()
def provider(upstream):
    registered = Provider(
        name="rcp",
        base_url=f"{upstream.url}/v1",
        api_key="provider-key",
        device="EPFL RCP",
        prefix="RCP-AIaaS",
    )
    passthrough_service._reset_cache_for_tests()
    with patch.object(
        passthrough_service, "registered_providers", return_value=[registered]
    ):
        yield registered
    passthrough_service._reset_cache_for_tests()


@pytest.fixture()
def auth(client, monkeypatch):
    """A real key, issued by the real first sign-in. Only the IdP is faked."""
    from backend.routers import profile as profile_router

    email_address = f"audio-{uuid.uuid4().hex[:8]}@example.org"
    monkeypatch.setattr(
        profile_router,
        "get_profile_from_accesstoken",
        lambda access_token: {"email": email_address, "sub": "test"},
    )
    profile = client.get(
        "/v1/profile", headers={"Authorization": "Bearer idp-access-token"}
    )
    assert profile.status_code == 200, profile.text
    return {"Authorization": f"Bearer {profile.json()['api_key']}"}


def _post(client, headers, model, operation="transcriptions", **fields):
    return client.post(
        f"/v1/audio/{operation}",
        headers=headers,
        data={"model": model, **fields},
        files={"file": (WAV_NAME, WAV, "audio/wav")},
    )


@pytest.mark.parametrize("operation", ["transcriptions", "translations"])
def test_wav_reaches_provider_intact(client, auth, upstream, provider, operation):
    resp = _post(
        client, auth, PUBLIC_MODEL, operation, response_format="srt", language="en"
    )

    assert resp.status_code == 200, resp.text
    assert resp.content == SRT
    assert resp.headers["content-type"].startswith("text/plain")

    assert len(upstream.requests) == 1
    sent = upstream.requests[0]
    assert sent["path"] == f"/v1/audio/{operation}"
    assert sent["authorization"] == "Bearer provider-key"
    parts = sent["parts"]
    assert parts["file"].get_payload(decode=True) == WAV
    assert parts["file"].get_filename() == WAV_NAME
    assert parts["file"].get_content_type() == "audio/wav"
    assert parts["model"].get_payload() == UPSTREAM_MODEL
    assert parts["response_format"].get_payload() == "srt"
    assert parts["language"].get_payload() == "en"


@pytest.mark.parametrize("name, content_type", AUDIO_FILES)
def test_every_format_reaches_provider_intact(
    client, auth, upstream, provider, name, content_type
):
    audio = (AUDIO_DIR / name).read_bytes()
    resp = client.post(
        "/v1/audio/transcriptions",
        headers=auth,
        data={"model": PUBLIC_MODEL},
        files={"file": (name, audio, content_type)},
    )

    assert resp.status_code == 200, resp.text
    sent = upstream.requests[0]["parts"]["file"]
    assert sent.get_payload(decode=True) == audio
    assert sent.get_filename() == name
    assert sent.get_content_type() == content_type


def test_wav_reaches_opentela_with_the_callers_key(client, auth, upstream):
    """The case reported in #154: a user launch on OpenTela."""

    class Settings:
        otela_head_addr = upstream.url

    with patch.object(route_service, "get_settings", return_value=Settings()):
        resp = _post(client, auth, USER_MODEL)

    assert resp.status_code == 200, resp.text
    sent = upstream.requests[0]
    assert sent["path"] == "/v1/service/llm/v1/audio/transcriptions"
    assert sent["authorization"] == auth["Authorization"]
    assert sent["parts"]["file"].get_payload(decode=True) == WAV
    assert sent["parts"]["model"].get_payload() == USER_MODEL


def test_upstream_html_error_uses_the_envelope(client, auth, upstream, provider):
    upstream.status = 404
    upstream.body = b"<html><body><h1>404 Not Found</h1></body></html>"
    upstream.content_type = "text/html"

    resp = _post(client, auth, PUBLIC_MODEL)

    assert resp.status_code == 404
    assert resp.json()["error"] == {
        "message": (
            f"upstream EPFL RCP returned 404 for model {UPSTREAM_MODEL} "
            "on /v1/audio/transcriptions"
        ),
        "type": "upstream_error",
        "param": None,
        "code": None,
    }


def test_bad_key_never_reaches_upstream(client, upstream, provider):
    headers = {"Authorization": "Bearer sk-rc-not-a-real-key"}
    resp = _post(client, headers, PUBLIC_MODEL)

    assert resp.status_code == 401
    assert upstream.requests == []
