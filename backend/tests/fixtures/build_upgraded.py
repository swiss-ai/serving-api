#!/usr/bin/env python3
"""Build dnt_table_upgraded.json from dnt_table_prod.json.

Adds the new Peer fields (hostname, version, status, labels) as if the
v0.0.6 OpenTela binary plus the model-launch --label changes had been
deployed:

- Every peer gets a synthetic SLURM job id (= its own worker_group_id).
- Each peer's metadata reflects realistic launched_by / framework /
  framework_args values, varied per model.
- started_at and expires_at are spread across "now-ish" so the UI shows
  a realistic mix of recently-launched and longer-running replicas.
- One multi-peer model is manually re-keyed so two of its peers share a
  worker_group_id, simulating a 2-node TP replica with a metrics-only
  follower (no `service`). This lets us exercise the multi-node-replica
  aggregation path on the frontend without needing live multi-node data.

Re-run after refreshing dnt_table_prod.json:
    python3 build_upgraded.py
"""

import json
import pathlib
from datetime import datetime, timedelta, timezone

HERE = pathlib.Path(__file__).parent
SRC = HERE / "dnt_table_prod.json"
DST = HERE / "dnt_table_upgraded.json"

# Plausible owners cycling through real users so the UI shows multiple.
USERS = ["rosmith", "xyao", "aahadinia", "isternfel", "yiswang"]

# Models whose served name suggests a particular launcher.
FRAMEWORK_HINTS = {
    "sglang": ["Apertus", "GLM", "gemma", "olmo", "gpt-oss"],
    "vllm": ["Qwen", "Llama", "Snowflake", "Kimi"],
}

# Representative framework_args per model. Covers what an operator
# actually types — paths, tensor-parallel sizing, memory caps. Real OCF
# emits these verbatim via `--label framework_args="..."`. Fixture-only
# until opentela patch lands the framework_args label.
FRAMEWORK_ARGS = {
    "Apertus-70B-Instruct-2509": (
        "--model-path /capstor/store/cscs/swissai/infra01/hf_models/models/swiss-ai/Apertus-70B-Instruct-2509 "
        "--tensor-parallel-size 4 --max-model-len 65536 --port 8080"
    ),
    "Apertus-8B-Instruct-2509": (
        "--model-path /capstor/store/cscs/swissai/infra01/hf_models/models/swiss-ai/Apertus-8B-Instruct-2509 "
        "--port 8080 --enable-metrics"
    ),
    "gemma-4-31B-it": (
        "--model-path /capstor/store/cscs/swissai/infra01/hf_models/models/google/gemma-4-31B-it "
        "--tensor-parallel-size 4 --port 8080"
    ),
    "Qwen3.5-397B-A17B": (
        "--model /capstor/store/cscs/swissai/infra01/hf_models/models/Qwen/Qwen3.5-397B-A17B "
        "--tensor-parallel-size 4 --max-model-len 32768 --gpu-memory-utilization 0.85 --port 8080"
    ),
    "gpt-oss-120b": (
        "--model-path /capstor/store/cscs/swissai/infra01/hf_models/models/openai/gpt-oss-120b "
        "--tensor-parallel-size 4 --port 8080 --reasoning-parser openai-oss"
    ),
    "Qwen3-32B": (
        "--model /capstor/store/cscs/swissai/infra01/hf_models/models/Qwen/Qwen3-32B "
        "--tensor-parallel-size 4 --port 8080"
    ),
    "Llama-3.3-70B-Instruct": (
        "--model /capstor/store/cscs/swissai/infra01/hf_models/models/meta-llama/Llama-3.3-70B-Instruct "
        "--tensor-parallel-size 4 --max-model-len 8192 --port 8080"
    ),
    "Qwen3-Next-80B-A3B-Instruct": (
        "--model /capstor/store/cscs/swissai/infra01/hf_models/models/Qwen/Qwen3-Next-80B-A3B-Instruct "
        "--tensor-parallel-size 4 --port 8080"
    ),
    "snowflake-arctic-embed-l-v2.0": (
        "--model /capstor/store/cscs/swissai/infra01/hf_models/models/Snowflake/snowflake-arctic-embed-l-v2.0 "
        "--task embed --port 8080"
    ),
    "GLM-4.7-Flash": (
        "--model-path /capstor/store/cscs/swissai/infra01/hf_models/models/zai-org/GLM-4.7-Flash --port 8080"
    ),
    "Qwen3.5-27B": (
        "--model /capstor/store/cscs/swissai/infra01/hf_models/models/Qwen/Qwen3.5-27B "
        "--tensor-parallel-size 2 --port 8080"
    ),
}


def guess_framework(model: str) -> str:
    for fw, hints in FRAMEWORK_HINTS.items():
        if any(h in model for h in hints):
            return fw
    return "sglang"


def guess_framework_args(model: str) -> str:
    """Find the best-matching entry in FRAMEWORK_ARGS for this served name."""
    for key, args in FRAMEWORK_ARGS.items():
        if key in model:
            return args
    return "--port 8080"


# Baseline "now" so the fixture is deterministic across regenerations.
NOW = datetime(2026, 5, 17, 13, 0, tzinfo=timezone.utc)


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

        # Spread launches over the past few hours: 30 min apart starting
        # 6 h ago. Plausibly varied; older launches expire sooner.
        started_offset = timedelta(minutes=30 * (i % 12) + 5 * (i // 12))
        started_at = NOW - timedelta(hours=6) + started_offset
        # Pick a SLURM time-limit consistent with how the launcher is
        # actually used today — short jobs (1 h) for quick tests,
        # long ones (12 h) for stable serving. Mix them.
        time_limit = timedelta(hours=12 if i % 3 == 0 else 1 if i % 7 == 0 else 6)
        expires_at = started_at + time_limit

        peer["labels"] = {
            "launched_by": USERS[i % len(USERS)],
            "slurm_job_id": str(job_id),
            "slurm_partition": "normal",
            "slurm_reservation": "SD-69241-apertus-1-5-0" if i % 4 == 0 else "",
            "worker_group_id": str(job_id),
            "framework": guess_framework(model_name) if model_name else "",
            "framework_args": guess_framework_args(model_name) if model_name else "",
            "served_model_name": model_name,
            "started_at": started_at.isoformat().replace("+00:00", "Z"),
            "expires_at": expires_at.isoformat().replace("+00:00", "Z"),
        }
        # Drop empty entries so the JSON looks closer to what OpenTela emits.
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
            head_labels = upgraded[head_key]["labels"]
            shared = head_labels["worker_group_id"]
            f_labels = upgraded[follower_key]["labels"]
            f_labels["worker_group_id"] = shared
            f_labels["slurm_job_id"] = shared
            f_labels["launched_by"] = head_labels["launched_by"]
            f_labels["started_at"] = head_labels["started_at"]
            f_labels["expires_at"] = head_labels["expires_at"]
            # Metrics-only: drop the service advertisement.
            upgraded[follower_key]["service"] = []
            upgraded[follower_key]["status"] = "ready"
            multi_node_assigned = True
            print(
                f"multi-node demo: {model} → head={head_key}, follower={follower_key}, wg={shared}"
            )
            break
    assert multi_node_assigned, "No model has >=2 peers; cannot demo multi-node"

    DST.write_text(json.dumps(upgraded, indent=2))
    print(f"wrote {DST} ({len(upgraded)} peers)")


if __name__ == "__main__":
    main()
