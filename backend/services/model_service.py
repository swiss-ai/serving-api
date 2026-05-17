import json
import pathlib

import requests

from backend.config import parse_hardware_info


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
        "version": node_info.get("version", ""),
        "status": node_info.get("status", ""),
        "labels": labels,
        # Convenience pulls — frontends can just read these directly
        # without having to dig into labels every time.
        "worker_group_id": labels.get("worker_group_id", ""),
        "launched_by": labels.get("launched_by", ""),
        "slurm_job_id": labels.get("slurm_job_id", ""),
        "framework": labels.get("framework", ""),
        "started_at": labels.get("started_at", ""),
    }


def _load_dnt(endpoint: str) -> dict:
    """Fetch DNT data. If endpoint points at a local file (no scheme), read
    it as JSON — that's the fixture-mode dev path. Otherwise HTTP-GET it."""
    if endpoint and not endpoint.startswith(("http://", "https://")):
        return json.loads(pathlib.Path(endpoint).read_text())
    return requests.get(endpoint).json()


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
            entry = {
                "id": "",  # no model yet
                "object": "model",
                "created": "0x",
                "owner": "0x",
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
                    **meta,
                }
                if with_details:
                    entry["device"] = device_info
                models.append(entry)
    return models
