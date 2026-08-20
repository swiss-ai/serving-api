from unittest.mock import patch

from backend.services import llm_service
from backend.services.llm_service import _upstream_timeout


def _settings(total: int, stall: int):
    class _S:
        upstream_timeout_seconds = total
        upstream_stream_stall_seconds = stall

    return patch.object(llm_service, "get_settings", return_value=_S())


def test_streaming_has_no_total_cap():
    """A long generation is not a failure. aiohttp's ``total`` spans every
    streamed chunk, so any total at all caps output length — bound the gap
    between chunks instead."""
    with _settings(3600, 300):
        timeout = _upstream_timeout(stream=True)
    assert timeout.total is None
    assert timeout.sock_read == 300


def test_non_streaming_caps_total():
    """Nothing arrives until the completion is done, so there is no chunk gap
    to measure and an overall cap is the only available bound."""
    with _settings(3600, 300):
        timeout = _upstream_timeout(stream=False)
    assert timeout.total == 3600
    assert timeout.sock_read is None


def test_never_inherits_the_aiohttp_default():
    """Regression guard for the bug this replaced: a bare ClientSession()
    inherits total=300 and silently truncates every generation past five
    minutes, streaming included."""
    with _settings(3600, 300):
        assert _upstream_timeout(stream=True).total != 300
        assert _upstream_timeout(stream=False).total != 300


def test_connect_stays_bounded():
    """An unreachable upstream must fail fast even though the response
    itself is allowed to take a long time."""
    with _settings(3600, 300):
        assert _upstream_timeout(stream=True).sock_connect == 30
        assert _upstream_timeout(stream=False).sock_connect == 30
