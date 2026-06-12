"""Unit tests for the json_body request-parsing dependency: malformed or
non-object bodies must surface as a clean 400, not an opaque 500 (the
classify `curl -d ''` bug)."""

import asyncio
import json

import pytest
from fastapi import HTTPException

from backend.middleware.body import json_body


class _FakeRequest:
    """Minimal stand-in for starlette.Request: json_body only ever awaits
    ``request.json()``, so we just script what that call does."""

    def __init__(self, *, returns=None, raises=None):
        self._returns = returns
        self._raises = raises

    async def json(self):
        if self._raises is not None:
            raise self._raises
        return self._returns


def _run(coro):
    return asyncio.run(coro)


def test_json_body_accepts_object():
    data = _run(json_body(_FakeRequest(returns={"model": "x"})))
    assert data == {"model": "x"}


def test_json_body_empty_body_is_400():
    """An empty body (curl -d '') makes request.json() raise — this is the
    classify 500 we're fixing. It must become a clean 400."""
    req = _FakeRequest(raises=json.JSONDecodeError("Expecting value", "", 0))
    with pytest.raises(HTTPException) as exc:
        _run(json_body(req))
    assert exc.value.status_code == 400


@pytest.mark.parametrize("body", [[], "hello", 5, None, True])
def test_json_body_non_object_is_400(body):
    """Valid JSON that isn't an object would blow up on data.get(...) — also
    a 400, not a 500."""
    with pytest.raises(HTTPException) as exc:
        _run(json_body(_FakeRequest(returns=body)))
    assert exc.value.status_code == 400
