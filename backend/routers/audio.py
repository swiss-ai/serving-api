import aiohttp
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from starlette.datastructures import UploadFile

from backend.middleware.auth import require_auth
from backend.middleware.model_id import require_namespaced_model
from backend.services.llm_service import llm_proxy_audio, response_passthrough
from backend.services.route_service import resolve_route

router = APIRouter()

# OpenAI's audio endpoints take multipart/form-data, not JSON: the model id
# is a form field next to the uploaded file, so routing reads it from the
# form. Every field is forwarded as received (language, prompt,
# response_format, temperature, timestamp_granularities[], ...) with only
# `model` swapped for the upstream id. vLLM serves these under /v1 like
# chat/completions, so the OpenAI-style base applies (server_root=False).


async def _audio_proxy(request: Request, token: str, operation: str):
    form = await request.form()
    public_model = require_namespaced_model(form.get("model"))
    if not isinstance(form.get("file"), UploadFile):
        raise HTTPException(
            status_code=400,
            detail="Request must be multipart/form-data with an audio 'file' field.",
        )

    route = await resolve_route(public_model, token)
    model = route.upstream_model(public_model)

    upstream_form = aiohttp.FormData()
    for name, value in form.multi_items():
        if isinstance(value, UploadFile):
            upstream_form.add_field(
                name,
                await value.read(),
                filename=value.filename or "audio",
                content_type=value.content_type or "application/octet-stream",
            )
        else:
            upstream_form.add_field(name, model if name == "model" else value)
    stream = str(form.get("stream", "")).lower() in ("true", "1")

    response = await llm_proxy_audio(
        endpoint=route.endpoint,
        api_key=route.api_key,
        operation=operation,
        form=upstream_form,
        stream=stream,
        model=model,
        provider_label=route.provider_label,
    )

    if stream:
        return StreamingResponse(
            response_passthrough(response), media_type="text/event-stream"
        )
    # Verbatim: response_format may be text, srt or vtt, not just JSON.
    return Response(content=response.body, media_type=response.media_type)


@router.post("/v1/audio/transcriptions")
async def transcriptions(request: Request, token: str = Depends(require_auth)):
    return await _audio_proxy(request, token, "transcriptions")


@router.post("/v1/audio/translations")
async def translations(request: Request, token: str = Depends(require_auth)):
    return await _audio_proxy(request, token, "translations")
