"""Canonical model-id shape enforcement at the request boundary.

Every routable model id on this platform is exactly three
slash-separated segments — ``<namespace>/<model_org>/<model_name>`` —
where the namespace is the platform prefix (``SwissAI-Research``), a
passthrough provider prefix (``CSCS-Inference``, ``RCP-AIaaS``), or a
username (user launches). Anything else — historical bare upstream ids,
partial ids, empty ids — is refused here with a 404 before any routing
decision, so a malformed id can never be silently claimed by whichever
upstream happens to advertise it (registration order used to pick the
winner).
"""

import logging

from fastapi import HTTPException

logger = logging.getLogger(__name__)


def require_namespaced_model(model_id) -> str:
    """Return the id unchanged when it has the canonical three-segment
    shape, else raise 404. The warning log is the operator's view of
    unmigrated clients — the response body carries the remedy."""
    parts = model_id.split("/") if isinstance(model_id, str) else []
    if len(parts) == 3 and all(parts):
        return model_id
    logger.warning(
        "Refusing model id %r: not <namespace>/<model_org>/<model_name>", model_id
    )
    raise HTTPException(
        status_code=404,
        detail=(
            f"Model '{model_id}' not found. Model ids must be fully namespaced "
            "as <namespace>/<model_org>/<model_name> "
            "(e.g. CSCS-Inference/swiss-ai/Apertus-8B-Instruct-2509) — "
            "see /v1/models for the available ids."
        ),
    )
