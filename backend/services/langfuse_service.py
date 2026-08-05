"""Fire-and-forget trace emission to the self-hosted Langfuse.

Uses the raw ingestion HTTP API rather than the langfuse SDK: the payload is
three fields and a POST, and pinning our behavior to the wire format avoids
SDK v2/v3 API churn. Emission is best-effort — a Langfuse outage must never
fail or slow a user's request, so everything is swallowed into a warning log
and the POST runs as a detached asyncio task.
"""

import asyncio
import base64
import logging
import time
import uuid
from typing import Any, Optional

import aiohttp

from backend.config import get_settings
from backend.services.monitoring_service import (
    resolve_owner_email,
    resolve_trace_level,
)

logger = logging.getLogger(__name__)


def _auth_header() -> Optional[str]:
    settings = get_settings()
    if not (
        settings.langfuse_host
        and settings.langfuse_public_key
        and settings.langfuse_secret_key
    ):
        return None
    raw = f"{settings.langfuse_public_key}:{settings.langfuse_secret_key}"
    return "Basic " + base64.b64encode(raw.encode()).decode()


async def _post_ingestion(batch: list[dict]) -> None:
    auth = _auth_header()
    if auth is None:
        return
    url = f"{get_settings().langfuse_host.rstrip('/')}/api/public/ingestion"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json={"batch": batch},
                headers={"Authorization": auth},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status not in (200, 201, 207):
                    logger.warning(
                        "Langfuse ingestion returned %s: %s",
                        resp.status,
                        (await resp.text())[:300],
                    )
    except Exception as exc:
        logger.warning("Langfuse ingestion failed: %s", exc)


def prepare_stream_trace(
    engine,
    api_key: str,
    model: str,
    request_data: dict,
    app_title: str = "",
) -> Optional[dict]:
    """Trace context for a streamed request, resolved before the stream
    starts. response_generator carries it and record_stream_result emits the
    single, complete trace after the last chunk — so streamed traces get
    output text, real usage, full-stream latency and TTFT, unlike an
    emit-at-headers approach."""
    try:
        email = resolve_owner_email(engine, api_key)
        if not email:
            return None
        level, is_default = resolve_trace_level(engine, email)
        ctx: dict = {
            "email": email,
            "level": level,
            "is_default": is_default,
            "model": model,
            "app_title": app_title,
            "start_time": time.time(),
        }
        if level == "full":
            ctx["input"] = request_data.get("messages") or request_data.get("prompt")
        return ctx
    except Exception as exc:
        logger.warning("prepare_stream_trace failed: %s", exc)
        return None


def record_stream_result(
    trace_ctx: dict,
    output_text: str,
    completion_tokens: int = 0,
    ttft_s: Optional[float] = None,
) -> None:
    """Emit the complete trace for a finished (or aborted) stream. Runs in
    response_generator's finally block, so partial output from a client
    disconnect is still recorded."""
    try:
        latency_s = time.time() - trace_ctx["start_time"]
        body: dict = {
            "id": str(uuid.uuid4()),
            "name": "chat-completion",
            "userId": trace_ctx["email"],
            "metadata": {
                "model": trace_ctx["model"],
                "level": trace_ctx["level"],
                "rule": not trace_ctx["is_default"],
                "streamed": True,
                "app_title": trace_ctx["app_title"],
                "latency_ms": latency_s * 1000,
                "ttft_ms": ttft_s * 1000 if ttft_s is not None else None,
                "source": "serving-api",
            },
        }
        if completion_tokens:
            body["metadata"]["usage"] = {"completion_tokens": completion_tokens}
        if trace_ctx["level"] == "full":
            body["input"] = trace_ctx.get("input")
            body["output"] = output_text
        event = {
            "id": str(uuid.uuid4()),
            "type": "trace-create",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
            "body": body,
        }
        asyncio.get_running_loop().create_task(_post_ingestion([event]))
    except Exception as exc:
        logger.warning("record_stream_result failed: %s", exc)


def record_if_monitored(
    engine,
    api_key: str,
    model: str,
    request_data: dict,
    response_data: Optional[Any] = None,
    streamed: bool = False,
    app_title: str = "",
    latency_ms: Optional[float] = None,
) -> None:
    """Emit a trace for this request. Default policy: everyone is traced at
    'metadata' (content-free: model/usage/latency — per-user token
    accounting, not optional); an active monitoring rule overrides the
    default, typically escalating to 'full' (prompt messages + non-streamed
    completion text). Synchronous rule lookup is ~free (30s cache); the
    network I/O is detached.
    """
    try:
        email = resolve_owner_email(engine, api_key)
        if not email:
            return
        level, is_default = resolve_trace_level(engine, email)

        trace_id = str(uuid.uuid4())
        body: dict = {
            "id": trace_id,
            "name": "chat-completion" if "messages" in request_data else "completion",
            "userId": email,
            "metadata": {
                "model": model,
                "level": level,
                "rule": not is_default,
                "streamed": streamed,
                "app_title": app_title,
                "latency_ms": latency_ms,
                "source": "serving-api",
            },
        }
        usage = None
        if response_data is not None and isinstance(response_data, dict):
            usage = response_data.get("usage")
        if usage:
            body["metadata"]["usage"] = usage

        if level == "full":
            body["input"] = request_data.get("messages") or request_data.get("prompt")
            if not streamed and isinstance(response_data, dict):
                choices = response_data.get("choices") or []
                if choices:
                    first = choices[0]
                    body["output"] = (first.get("message") or {}).get(
                        "content"
                    ) or first.get("text")

        event = {
            "id": str(uuid.uuid4()),
            "type": "trace-create",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
            "body": body,
        }
        asyncio.get_running_loop().create_task(_post_ingestion([event]))
    except Exception as exc:
        # Monitoring must never break the request path.
        logger.warning("record_if_monitored failed: %s", exc)
