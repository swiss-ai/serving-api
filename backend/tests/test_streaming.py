import asyncio
import json

from backend.services.llm_service import response_generator


async def _aiter(lines):
    for line in lines:
        yield line


def _collect(lines):
    async def run():
        return [chunk async for chunk in response_generator(_aiter(lines))]

    return asyncio.run(run())


def test_stream_reemits_done_terminator():
    """The gateway must forward the OpenAI SSE terminator (#72) rather than
    swallowing it — clients rely on `data: [DONE]` to know the stream ended."""
    lines = [
        b'data: {"choices": [{"delta": {"content": "hi"}}]}',
        b"data: [DONE]",
    ]
    chunks = _collect(lines)
    assert chunks[-1] == "data: [DONE]\n\n"


def test_stream_preserves_leading_whitespace():
    """Content must pass through byte-identical; the first delta must not be
    lstrip()ed (#75)."""
    lines = [
        b'data: {"choices": [{"delta": {"content": "\\n  hello"}}]}',
        b"data: [DONE]",
    ]
    chunks = _collect(lines)
    payload = json.loads(chunks[0][len("data: ") :])
    assert payload["choices"][0]["delta"]["content"] == "\n  hello"


def test_stream_preserves_leading_whitespace_completions():
    """Same byte-identical guarantee for /v1/completions `text` deltas (#75)."""
    lines = [
        b'data: {"choices": [{"text": "  spaced"}]}',
        b"data: [DONE]",
    ]
    chunks = _collect(lines)
    payload = json.loads(chunks[0][len("data: ") :])
    assert payload["choices"][0]["text"] == "  spaced"
