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


def _normalize_usage(usage: Optional[dict]) -> Optional[dict]:
    """OpenAI-style usage -> Langfuse generation usage keys."""
    if not isinstance(usage, dict):
        return None
    prompt = usage.get("prompt_tokens", usage.get("promptTokens"))
    completion = usage.get("completion_tokens", usage.get("completionTokens"))
    total = usage.get("total_tokens", usage.get("totalTokens"))
    if total is None and (prompt is not None or completion is not None):
        total = (prompt or 0) + (completion or 0)
    if prompt is None and completion is None and total is None:
        return None
    out = {}
    if prompt is not None:
        out["promptTokens"] = prompt
    if completion is not None:
        out["completionTokens"] = completion
    if total is not None:
        out["totalTokens"] = total
    return out


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(ts))


def _generation_event(
    trace_id: str,
    model: str,
    start_ts: float,
    end_ts: float,
    usage: Optional[dict],
    input_data=None,
    output_data=None,
) -> dict:
    """generation-create linked to the trace. Langfuse's analytics (token
    aggregations, daily metrics usage-by-model, dashboards) key off
    generation observations — bare traces are invisible to them."""
    body: dict = {
        "id": str(uuid.uuid4()),
        "traceId": trace_id,
        "name": "llm-call",
        "model": model,
        "startTime": _iso(start_ts),
        "endTime": _iso(end_ts),
    }
    norm = _normalize_usage(usage)
    if norm:
        body["usage"] = norm
    if input_data is not None:
        body["input"] = input_data
    if output_data is not None:
        body["output"] = output_data
    return {
        "id": str(uuid.uuid4()),
        "type": "generation-create",
        "timestamp": _iso(time.time()),
        "body": body,
    }


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
    emit-at-headers approach.

    The ctx also carries email/model into usage accounting, which counts
    every request — so a ctx is returned for every resolvable caller even
    when level is None (unmonitored). The Langfuse gate lives inside
    record_stream_result, never here: returning None for ungated users
    would silently stop their streamed usage from being counted."""
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
    usage: Optional[dict] = None,
    ttft_s: Optional[float] = None,
) -> None:
    """Emit the complete trace for a finished (or aborted) stream. Runs in
    response_generator's finally block, so partial output from a client
    disconnect is still recorded. No-op for unmonitored users (level None)
    — this is THE Langfuse gate for streams; the ctx itself always exists
    because usage accounting rides on it."""
    try:
        if not trace_ctx.get("level"):
            return
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
        if usage:
            body["metadata"]["usage"] = usage
        full = trace_ctx["level"] == "full"
        if full:
            body["input"] = trace_ctx.get("input")
            body["output"] = output_text
        trace_event = {
            "id": str(uuid.uuid4()),
            "type": "trace-create",
            "timestamp": _iso(time.time()),
            "body": body,
        }
        gen_event = _generation_event(
            body["id"],
            trace_ctx["model"],
            trace_ctx["start_time"],
            time.time(),
            usage,
            input_data=trace_ctx.get("input") if full else None,
            output_data=output_text if full else None,
        )
        asyncio.get_running_loop().create_task(
            _post_ingestion([trace_event, gen_event])
        )
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
    """Emit a trace for this request — only when the user has an active
    monitoring rule (superadmin-created or self-serve opt-in). Without a
    rule nothing is written: Langfuse stores deliberate recordings, not
    ambient telemetry; token accounting is Postgres usage_daily. 'metadata'
    records model/usage/latency, 'full' adds prompt messages and
    non-streamed completion text. Synchronous rule lookup is ~free (30s
    cache); the network I/O is detached.
    """
    try:
        email = resolve_owner_email(engine, api_key)
        if not email:
            return
        level, is_default = resolve_trace_level(engine, email)
        if level is None:
            return

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
        # Non-streamed responses may be pydantic models (ModelResponse), not
        # dicts — normalize before reading usage/choices.
        if response_data is not None and not isinstance(response_data, dict):
            if hasattr(response_data, "model_dump"):
                response_data = response_data.model_dump()
            else:
                response_data = None
        usage = None
        if response_data is not None:
            usage = response_data.get("usage")
        if usage:
            body["metadata"]["usage"] = usage

        output = None
        if level == "full":
            body["input"] = request_data.get("messages") or request_data.get("prompt")
            if not streamed and isinstance(response_data, dict):
                choices = response_data.get("choices") or []
                if choices:
                    first = choices[0]
                    output = (first.get("message") or {}).get("content") or first.get(
                        "text"
                    )
                    body["output"] = output

        now = time.time()
        events = [
            {
                "id": str(uuid.uuid4()),
                "type": "trace-create",
                "timestamp": _iso(now),
                "body": body,
            },
            _generation_event(
                trace_id,
                model,
                now - (latency_ms or 0) / 1000,
                now,
                usage,
                input_data=body.get("input") if level == "full" else None,
                output_data=output,
            ),
        ]
        asyncio.get_running_loop().create_task(_post_ingestion(events))
    except Exception as exc:
        # Monitoring must never break the request path.
        logger.warning("record_if_monitored failed: %s", exc)
