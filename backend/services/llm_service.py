import json
import aiohttp
from typing import Dict, Union
from backend.models.protocols import (
    ModelResponse,
    BackendHTTPError,
    LLMRequest,
    LLMCompletionsRequest,
)
from backend.services.metrics_service import metrics_collector
import time

active_requests = 0


async def response_generator(response, metrics_ctx=None):
    accumulated_content = []
    has_started_content = False
    first_token_time = None
    token_count = 0

    start_time = None
    model = None
    node_id = None
    dnt_endpoint = None
    concurrency = 0

    if metrics_ctx:
        start_time = metrics_ctx.get("start_time")
        model = metrics_ctx.get("model")
        node_id = metrics_ctx.get("node_id")
        dnt_endpoint = metrics_ctx.get("dnt_endpoint")
        concurrency = metrics_ctx.get("concurrency")

    try:
        async for line in response:
            line = line.strip()
            if not line:
                continue
            if line.startswith(b"data: "):
                data_str = line[6:].decode("utf-8")
                if data_str == "[DONE]":
                    continue
                try:
                    data = json.loads(data_str)
                    if "choices" in data and len(data["choices"]) > 0:
                        choice = data["choices"][0]
                        if "delta" in choice and "content" in choice["delta"]:
                            original_content = choice["delta"]["content"]
                            if original_content:
                                processed_content = original_content
                                if not has_started_content:
                                    processed_content = original_content.lstrip()
                                    if processed_content:
                                        if not first_token_time:
                                            first_token_time = time.time()
                                        has_started_content = True
                                choice["delta"]["content"] = processed_content
                                if processed_content:
                                    accumulated_content.append(processed_content)

                        elif "text" in choice:
                            original_content = choice["text"]
                            if original_content:
                                processed_content = original_content
                                if not has_started_content:
                                    processed_content = original_content.lstrip()
                                    if processed_content:
                                        if not first_token_time:
                                            first_token_time = time.time()
                                        has_started_content = True
                                choice["text"] = processed_content
                                if processed_content:
                                    accumulated_content.append(processed_content)

                    if data.get("usage", None) is not None:
                        if "completion_tokens" in data["usage"]:
                            token_count = data["usage"]["completion_tokens"]

                    yield f"data: {json.dumps(data)}\n\n"
                except json.JSONDecodeError:
                    continue
    finally:
        if metrics_ctx and start_time and node_id:
            full_content = "".join(accumulated_content)
            end_time = time.time()
            latency = end_time - start_time
            ttft = (first_token_time - start_time) if first_token_time else latency

            if token_count == 0 and full_content:
                token_count = len(full_content) / 4.0

            throughput = token_count / latency if latency > 0 else 0

            metrics_collector.record(
                model=model,
                node_id=node_id,
                dnt_endpoint=dnt_endpoint,
                concurrency=concurrency,
                ttft=ttft,
                latency=latency,
                throughput=throughput,
            )

        global active_requests
        active_requests -= 1


async def response_generator_raw(response):
    """Raw SSE passthrough for the Responses API — no content parsing."""
    try:
        async for line in response:
            line = line.strip()
            if not line:
                continue
            yield line.decode("utf-8") + "\n"
    finally:
        global active_requests
        active_requests -= 1


class StreamWrapper:
    def __init__(self, gen, headers=None):
        self.gen = gen
        self.headers = headers

    def __aiter__(self):
        return self.gen


class RawResponse:
    """Wrapper for raw (non-ModelResponse) JSON responses."""

    def __init__(self, data: dict, headers: dict = None):
        self.data = data
        self.headers = headers or {}


async def _execute_http_request(
    session: aiohttp.ClientSession,
    url: str,
    headers: Dict,
    payload: Dict,
    stream: bool,
    raw_response: bool = False,
) -> Union[ModelResponse, StreamWrapper, RawResponse]:
    req_cm = session.post(url, json=payload, headers=headers)
    try:
        resp = await req_cm.__aenter__()
    except Exception as e:
        await session.close()
        raise e

    if resp.status >= 400:
        try:
            text = await resp.text()
        except Exception:
            text = str(resp.status)
        await req_cm.__aexit__(None, None, None)
        await session.close()
        raise BackendHTTPError(status_code=resp.status, body=text)

    response_headers = dict(resp.headers)
    if stream:

        async def wrapped_content():
            try:
                async for chunk in resp.content:
                    yield chunk
            finally:
                await req_cm.__aexit__(None, None, None)
                await session.close()

        return StreamWrapper(wrapped_content(), headers=response_headers)
    else:
        try:
            data = await resp.json()
        finally:
            await req_cm.__aexit__(None, None, None)
            await session.close()

        if raw_response:
            return RawResponse(data=data, headers=response_headers)

        model_response = ModelResponse(**data)
        model_response.headers = response_headers
        return model_response


async def _shared_proxy_handler(
    endpoint: str,
    api_key: str,
    payload: Dict,
    headers_extra: Dict,
    stream: bool,
    full_url: str,
    model: str,
    raw_response: bool = False,
) -> Union[ModelResponse, StreamWrapper, RawResponse]:
    global active_requests
    active_requests += 1
    start_time = time.time()
    snapshot_concurrency = active_requests

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    headers.update(headers_extra)

    session = aiohttp.ClientSession()
    try:
        resp = await _execute_http_request(
            session=session,
            url=full_url,
            headers=headers,
            payload=payload,
            stream=stream,
            raw_response=raw_response,
        )
        node_id = (
            resp.headers.get("X-Computing-Node", "unknown")
            if hasattr(resp, "headers")
            else "unknown"
        )
        dnt_endpoint = endpoint.split("/service")[0] + "/dnt/table"
        if stream and isinstance(resp, StreamWrapper):
            resp.metrics_ctx = {
                "start_time": start_time,
                "model": model,
                "node_id": node_id,
                "dnt_endpoint": dnt_endpoint,
                "concurrency": snapshot_concurrency,
            }

        else:
            end_time = time.time()
            latency = end_time - start_time

            token_count = 0
            if isinstance(resp, ModelResponse) and resp.usage:
                token_count = resp.usage.completion_tokens
            elif isinstance(resp, RawResponse) and resp.data.get("usage"):
                token_count = resp.data["usage"].get("completion_tokens", 0)

            throughput = token_count / latency if latency > 0 else 0

            metrics_collector.record(
                model=model,
                node_id=node_id,
                dnt_endpoint=dnt_endpoint,
                concurrency=snapshot_concurrency,
                ttft=latency,
                latency=latency,
                throughput=throughput,
            )
            active_requests -= 1

        return resp

    except BackendHTTPError:
        active_requests -= 1
        if not session.closed:
            await session.close()
        raise
    except Exception:
        active_requests -= 1
        if not session.closed:
            await session.close()
        raise


async def llm_proxy(endpoint, api_key, request: LLMRequest) -> ModelResponse:
    return await _shared_proxy_handler(
        endpoint=endpoint,
        api_key=api_key,
        payload=request.to_payload(),
        headers_extra={},
        stream=request.stream,
        full_url=endpoint.rstrip("/") + "/chat/completions",
        model=request.model,
    )


async def llm_proxy_completions(
    endpoint, api_key, request: LLMCompletionsRequest
) -> ModelResponse:
    return await _shared_proxy_handler(
        endpoint=endpoint,
        api_key=api_key,
        payload=request.to_payload(),
        headers_extra={},
        stream=request.stream,
        full_url=endpoint.rstrip("/") + "/completions",
        model=request.model,
    )


async def llm_proxy_embeddings(endpoint, api_key, **kwargs) -> ModelResponse:
    embedding_params = {
        "model": kwargs.get("model"),
        "input": kwargs.get("input", []),
        "encoding_format": kwargs.get("encoding_format", "float"),
    }
    if kwargs.get("dimensions") is not None:
        embedding_params["dimensions"] = kwargs.get("dimensions")
    if kwargs.get("user") is not None:
        embedding_params["user"] = kwargs.get("user")

    return await _shared_proxy_handler(
        endpoint=endpoint,
        api_key=api_key,
        payload=embedding_params,
        headers_extra={},
        stream=False,
        full_url=endpoint.rstrip("/") + "/embeddings",
        model=kwargs.get("model"),
    )


async def llm_proxy_responses(
    endpoint, api_key, payload: dict, stream: bool, model: str
):
    return await _shared_proxy_handler(
        endpoint=endpoint,
        api_key=api_key,
        payload=payload,
        headers_extra={},
        stream=stream,
        full_url=endpoint.rstrip("/") + "/responses",
        model=model,
        raw_response=True,
    )


async def llm_proxy_rerank(endpoint, api_key, payload: dict, model: str):
    return await _shared_proxy_handler(
        endpoint=endpoint,
        api_key=api_key,
        payload=payload,
        headers_extra={},
        stream=False,
        full_url=endpoint.rstrip("/") + "/rerank",
        model=model,
        raw_response=True,
    )


async def llm_proxy_score(endpoint, api_key, payload: dict, model: str):
    return await _shared_proxy_handler(
        endpoint=endpoint,
        api_key=api_key,
        payload=payload,
        headers_extra={},
        stream=False,
        full_url=endpoint.rstrip("/") + "/score",
        model=model,
        raw_response=True,
    )


async def llm_proxy_tokenize(endpoint, api_key, payload: dict, model: str):
    return await _shared_proxy_handler(
        endpoint=endpoint,
        api_key=api_key,
        payload=payload,
        headers_extra={},
        stream=False,
        full_url=endpoint.rstrip("/") + "/tokenize",
        model=model,
        raw_response=True,
    )


async def llm_proxy_detokenize(endpoint, api_key, payload: dict, model: str):
    return await _shared_proxy_handler(
        endpoint=endpoint,
        api_key=api_key,
        payload=payload,
        headers_extra={},
        stream=False,
        full_url=endpoint.rstrip("/") + "/detokenize",
        model=model,
        raw_response=True,
    )
