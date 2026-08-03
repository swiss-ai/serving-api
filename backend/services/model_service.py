import json
import re
import pathlib

import requests

from backend.config import get_settings, parse_hardware_info
from backend.services.passthrough_service import PLATFORM_PREFIX


def _peer_metadata(node_info: dict) -> dict:
    """Pull the surfaced launch-time fields off a DNT peer entry.

    Older OpenTela binaries (<v0.0.6) don't emit hostname/status/labels —
    we return whatever's present and let consumers treat missing keys as
    'unknown'. labels.worker_group_id is what the frontend groups by to
    count replicas of a single model.
    """
    labels = node_info.get("labels") or {}
    return {
        "peer_id": node_info.get("id", ""),
        "hostname": node_info.get("hostname", ""),
        "otela_version": node_info.get("version", ""),
        "status": node_info.get("status", ""),
        "labels": labels,
        # Convenience pulls — frontends can just read these directly
        # without having to dig into labels every time.
        "worker_group_id": labels.get("worker_group_id", ""),
        "launched_by": labels.get("launched_by", ""),
        "authorization": labels.get("authorization", ""),
        "slurm_job_id": labels.get("slurm_job_id", ""),
        "framework": labels.get("framework", ""),
        "started_at": labels.get("started_at", ""),
        "expires_at": labels.get("expires_at", ""),
    }


def _load_dnt(endpoint: str) -> dict:
    """Fetch DNT data. If endpoint points at a local file (no scheme), read
    it as JSON — that's the fixture-mode dev path. Otherwise HTTP-GET it."""
    if endpoint and not endpoint.startswith(("http://", "https://")):
        return json.loads(pathlib.Path(endpoint).read_text())
    # Bounded, always: this is a sync call on the event loop, so with no
    # timeout an unreachable head (laptop off VPN, head down) hangs this
    # request forever AND queues every other request behind it — the whole
    # gateway reads as frozen. Callers already treat failure as "no models".
    return requests.get(endpoint, timeout=(3, 10)).json()


def _version_tuple(raw: str) -> tuple[int, ...] | None:
    """First dotted number in a version string, as a comparable tuple.
    "sai-v0.0.6" -> (0, 0, 6). None when there is no version to compare."""
    match = re.search(r"(\d+(?:\.\d+)*)", raw or "")
    if not match:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def _at_least(version: str, minimum: str) -> bool:
    """version >= minimum, comparing numerically and padding to equal length
    so sai-v0.0.10 sorts above sai-v0.0.6."""
    got, want = _version_tuple(version), _version_tuple(minimum)
    if want is None:
        return True
    if got is None:
        return False
    width = max(len(got), len(want))
    return got + (0,) * (width - len(got)) >= want + (0,) * (width - len(want))


def listing_exclusion(model: dict) -> str | None:
    """Why the public listing hides this entry — None when it is advertised.

    Launch ids are namespaced by their first segment: our own k8s launches
    become ``SwissAI-Research/<hf_org>/<hf_model>``, a user launching on the
    OpenTela network gets ``<username>/<hf_org>/<hf_model>``. An id must be
    exactly three non-empty segments, which also drops malformed entries
    such as a bare name or a checkpoint path.

    Ours are always listed. A user launch is listed only when its peer runs
    at least MIN_USER_OTELA_VERSION — older nodes predate namespaced ids.

    The reason string is surfaced verbatim on the admin all-models page,
    so keep it something a model owner can act on.
    """
    settings = get_settings()
    if not settings.enforce_model_namespace:
        return None
    segments = (model.get("id") or "").split("/")
    if len(segments) != 3 or not all(segments):
        return "id is not <owner>/<hf_org>/<hf_model> (exactly 3 segments)"
    if segments[0] == PLATFORM_PREFIX:
        return None
    if _at_least(model.get("otela_version") or "", settings.min_user_otela_version):
        return None
    return (
        f"otela_version {model.get('otela_version') or '(missing)'} is below "
        f"{settings.min_user_otela_version}"
    )


def platform_namespaced(models: list[dict]) -> list[dict]:
    """Filter /v1/models* down to what we are willing to advertise — the
    entries ``listing_exclusion`` has no complaint about.

    Listing only: everything keeps running and stays routable by id.
    Disable the whole filter with ENFORCE_MODEL_NAMESPACE=false.
    """
    return [m for m in models if listing_exclusion(m) is None]


def get_all_models(endpoint: str, with_details: bool = False):
    """Return one entry per (peer, model) pair served on the network.

    The frontend aggregates these by model id and by worker_group_id to
    produce the model card + replica count. We keep the granularity at the
    peer level so multi-node replicas show their full topology (head +
    metrics-only followers all share the same worker_group_id).
    """
    try:
        data = _load_dnt(endpoint)
    except Exception:
        return []
    models = []
    for node_info in data.values():
        meta = _peer_metadata(node_info)
        device_info = parse_hardware_info(node_info.get("hardware"))
        services = node_info.get("service") or []
        if not services:
            # Metrics-only / pending peer: surface it under a sentinel id so
            # the frontend can attribute it to the right replica via
            # worker_group_id and show it as part of a launching/follower set.
            if not meta["worker_group_id"]:
                continue
            # Fall back to the served_model_name label so the frontend can
            # group PENDING peers under their eventual model card during boot.
            # Without this, the brief PENDING window is invisible because the
            # peer has no advertised service yet and nothing else maps its
            # worker_group_id back to a model id.
            entry = {
                "id": meta["labels"].get("served_model_name", ""),
                "object": "model",
                "created": "0x",
                "owner": "0x",
                "has_service": False,
                **meta,
            }
            if with_details:
                entry["device"] = device_info
            models.append(entry)
            continue
        for service in services:
            if not service.get("identity_group"):
                continue
            model_names = [
                identity[len("model=") :]
                for identity in service["identity_group"]
                if identity.startswith("model=")
            ]
            for model_name in model_names:
                entry = {
                    "id": model_name,
                    "object": "model",
                    "created": "0x",
                    "owner": "0x",
                    "has_service": True,
                    **meta,
                }
                if with_details:
                    entry["device"] = device_info
                models.append(entry)
    return models
