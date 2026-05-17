#!/usr/bin/env python3
"""Build dnt_table_upgraded.json from dnt_table_prod.json.

Adds the new Peer fields (hostname, version, status, labels) as if the
v0.0.6 OCF binary plus the model-launch --label changes had been deployed:

- Every peer gets a synthetic SLURM job id (= its own worker_group_id).
- Each peer's metadata reflects realistic launched_by / framework values.
- One multi-peer model is manually re-keyed so two of its peers share a
  worker_group_id, simulating a 2-node TP replica with a metrics-only
  follower (no `service`). This lets us exercise the multi-node-replica
  aggregation path on the frontend without needing live multi-node data.

Re-run after refreshing dnt_table_prod.json:
    python3 build_upgraded.py
"""

import json
import pathlib

HERE = pathlib.Path(__file__).parent
SRC = HERE / "dnt_table_prod.json"
DST = HERE / "dnt_table_upgraded.json"

# Plausible owners cycling through real users so the UI shows multiple.
USERS = ["rosmith", "xyao", "aahadinia", "isternfel", "yiswang"]

# Models whose served name suggests a particular launcher.
FRAMEWORK_HINTS = {
    "sglang": ["Apertus", "GLM", "gemma", "olmo"],
    "vllm": ["Qwen", "Llama", "Snowflake", "Kimi", "Apertus-1.5"],
}


def guess_framework(model: str) -> str:
    for fw, hints in FRAMEWORK_HINTS.items():
        if any(h in model for h in hints):
            return fw
    return "sglang"


def main() -> None:
    src = json.loads(SRC.read_text())
    upgraded: dict = {}
    next_job_id = 2256000
    multi_node_assigned = False

    # Synthesize a stable ordering so re-runs produce stable diffs.
    for i, (pid_key, peer) in enumerate(sorted(src.items())):
        peer = dict(peer)  # shallow copy

        # Hostname based on peer ID, padded — looks like a real nidXXXXXX.
        peer["hostname"] = f"nid{(0x6000 + i):06d}"[-9:]
        peer["version"] = "v0.0.6"

        services = peer.get("service") or []
        model_name = ""
        for svc in services:
            for ig in svc.get("identity_group") or []:
                if ig.startswith("model="):
                    model_name = ig[6:]
                    break
            if model_name:
                break

        peer["status"] = "ready" if model_name else "pending"
        job_id = next_job_id
        next_job_id += 1

        peer["labels"] = {
            "launched_by": USERS[i % len(USERS)],
            "slurm_job_id": str(job_id),
            "slurm_partition": "normal",
            "worker_group_id": str(job_id),
            "framework": guess_framework(model_name) if model_name else "",
            "served_model_name": model_name,
            "started_at": "2026-05-15T18:00:00Z",
        }
        # Drop empty entries so the JSON looks closer to what OCF emits.
        peer["labels"] = {k: v for k, v in peer["labels"].items() if v}

        upgraded[pid_key] = peer

    # Demo a multi-node replica: pick a model with multiple peers and
    # collapse two of them into a single worker_group_id, with the second
    # becoming metrics-only (no service, no service entries).
    by_model: dict[str, list[str]] = {}
    for pid_key, peer in upgraded.items():
        for svc in peer.get("service") or []:
            for ig in svc.get("identity_group") or []:
                if ig.startswith("model="):
                    by_model.setdefault(ig[6:], []).append(pid_key)
    for model, peers in by_model.items():
        if len(peers) >= 2:
            head_key, follower_key = peers[0], peers[1]
            shared = upgraded[head_key]["labels"]["worker_group_id"]
            upgraded[follower_key]["labels"]["worker_group_id"] = shared
            upgraded[follower_key]["labels"]["slurm_job_id"] = shared
            upgraded[follower_key]["labels"]["launched_by"] = upgraded[head_key]["labels"]["launched_by"]
            # Metrics-only: drop the service advertisement.
            upgraded[follower_key]["service"] = []
            upgraded[follower_key]["status"] = "ready"
            multi_node_assigned = True
            print(f"multi-node demo: {model} → head={head_key}, follower={follower_key}, wg={shared}")
            break
    assert multi_node_assigned, "No model has >=2 peers; cannot demo multi-node"

    DST.write_text(json.dumps(upgraded, indent=2))
    print(f"wrote {DST} ({len(upgraded)} peers)")


if __name__ == "__main__":
    main()
