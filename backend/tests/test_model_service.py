"""Unit tests for get_all_models: aggregation of DNT peers into per-model
entries the frontend can consume."""

from unittest.mock import patch

from backend.services.model_service import get_all_models


def _dnt_response(peers: dict):
    """Build a fake requests.Response.json() for a DNT /v1/dnt/table call."""

    class FakeResp:
        def __init__(self, data):
            self._data = data

        def json(self):
            return self._data

    return FakeResp(peers)


PEER_NEW_BINARY_HEAD = {
    "id": "QmHead",
    "hostname": "nid006220",
    "version": "v0.0.6",
    "status": "ready",
    "labels": {
        "launched_by": "rosmith",
        "slurm_job_id": "12345",
        "worker_group_id": "12345",
        "framework": "sglang",
        "started_at": "2026-05-15T18:00:00Z",
    },
    "hardware": {"gpus": [{"name": "GH200"}] * 4},
    "service": [
        {
            "name": "llm",
            "status": "connected",
            "host": "localhost",
            "port": "8080",
            "identity_group": ["model=swiss-ai/Apertus-8B"],
        }
    ],
}

PEER_NEW_BINARY_FOLLOWER = {
    "id": "QmFollower",
    "hostname": "nid006221",
    "version": "v0.0.6",
    "status": "pending",
    "labels": {
        "launched_by": "rosmith",
        "slurm_job_id": "12345",
        "worker_group_id": "12345",
        "framework": "sglang",
        "started_at": "2026-05-15T18:00:00Z",
    },
    "hardware": {"gpus": [{"name": "GH200"}] * 4},
    "service": [],
}

PEER_OLD_BINARY = {
    "id": "QmOld",
    "hardware": {"gpus": [{"name": "GH200"}] * 4},
    "service": [
        {
            "name": "llm",
            "status": "connected",
            "host": "localhost",
            "port": "8080",
            "identity_group": ["model=legacy/Llama-70B"],
        }
    ],
}


def test_old_binary_peer_still_surfaces():
    with patch("backend.services.model_service.requests.get") as mock_get:
        mock_get.return_value = _dnt_response({"/QmOld": PEER_OLD_BINARY})
        out = get_all_models("http://x/v1/dnt/table", with_details=True)
    assert len(out) == 1
    assert out[0]["id"] == "legacy/Llama-70B"
    # Old binary contributes no metadata — frontend treats blanks as unknown.
    assert out[0]["hostname"] == ""
    assert out[0]["worker_group_id"] == ""


def test_new_binary_head_carries_labels():
    with patch("backend.services.model_service.requests.get") as mock_get:
        mock_get.return_value = _dnt_response({"/QmHead": PEER_NEW_BINARY_HEAD})
        out = get_all_models("http://x/v1/dnt/table", with_details=True)
    assert len(out) == 1
    entry = out[0]
    assert entry["id"] == "swiss-ai/Apertus-8B"
    assert entry["hostname"] == "nid006220"
    assert entry["launched_by"] == "rosmith"
    assert entry["worker_group_id"] == "12345"
    assert entry["framework"] == "sglang"
    assert entry["status"] == "ready"


def test_metrics_only_follower_groups_with_head_via_worker_group_id():
    """A multi-node replica's follower has no `service` but does carry
    worker_group_id. It should appear in the output with id='' so the
    frontend can attribute it to the same replica as the head."""
    with patch("backend.services.model_service.requests.get") as mock_get:
        mock_get.return_value = _dnt_response(
            {
                "/QmHead": PEER_NEW_BINARY_HEAD,
                "/QmFollower": PEER_NEW_BINARY_FOLLOWER,
            }
        )
        out = get_all_models("http://x/v1/dnt/table", with_details=True)
    assert len(out) == 2
    by_id = {e["peer_id"]: e for e in out}
    assert by_id["QmHead"]["id"] == "swiss-ai/Apertus-8B"
    assert by_id["QmFollower"]["id"] == ""
    # Shared worker_group_id lets the frontend group them.
    assert (
        by_id["QmHead"]["worker_group_id"]
        == by_id["QmFollower"]["worker_group_id"]
        == "12345"
    )


def test_follower_without_worker_group_id_skipped():
    """Older binary follower with no labels and no service is uninformative —
    drop it so the model list stays clean."""
    bare = {"id": "QmBare", "service": [], "hardware": {"gpus": []}}
    with patch("backend.services.model_service.requests.get") as mock_get:
        mock_get.return_value = _dnt_response({"/QmBare": bare})
        out = get_all_models("http://x/v1/dnt/table")
    assert out == []


def test_legacy_ocf_env_vars_still_work(monkeypatch):
    """OCF_HEAD_ADDR and OCF_FIXTURE_PATH must keep working through the
    rename to OTELA_*. Deployments can migrate on their own schedule."""
    from backend.config import Settings

    monkeypatch.setenv("OCF_HEAD_ADDR", "http://legacy:8092")
    monkeypatch.setenv("OCF_FIXTURE_PATH", "/legacy/fixture.json")
    monkeypatch.delenv("OTELA_HEAD_ADDR", raising=False)
    monkeypatch.delenv("OTELA_FIXTURE_PATH", raising=False)
    s = Settings()
    assert s.otela_head_addr == "http://legacy:8092"
    assert s.otela_fixture_path == "/legacy/fixture.json"


def test_canonical_otela_env_vars_win_over_legacy(monkeypatch):
    """When both are set, the canonical OTELA_* names win so a partial
    migration (one renamed, one not) doesn't silently keep the legacy
    value in force."""
    from backend.config import Settings

    monkeypatch.setenv("OCF_HEAD_ADDR", "http://legacy:8092")
    monkeypatch.setenv("OTELA_HEAD_ADDR", "http://canonical:8092")
    s = Settings()
    assert s.otela_head_addr == "http://canonical:8092"


def test_request_failure_returns_empty():
    with patch("backend.services.model_service.requests.get") as mock_get:
        mock_get.side_effect = Exception("boom")
        out = get_all_models("http://x/v1/dnt/table")
    assert out == []


# ── fixtures from live prod ─────────────────────────────────────────────────


def _load_fixture(name: str) -> dict:
    import json
    import pathlib

    p = pathlib.Path(__file__).parent / "fixtures" / name
    return json.loads(p.read_text())


def test_real_prod_payload_returns_models():
    """Pre-upgrade prod payload: every peer either has service entries (which
    surface as a model entry) or has no labels (so we drop the metrics-only
    fallback). End count should match what the dashboard shows today."""
    with patch("backend.services.model_service.requests.get") as mock_get:
        mock_get.return_value = type(
            "R", (), {"json": lambda self=None: _load_fixture("dnt_table_prod.json")}
        )()
        out = get_all_models("http://x/v1/dnt/table", with_details=True)
    # All non-empty ids should be model names from the live network.
    model_ids = {e["id"] for e in out if e["id"]}
    assert "swiss-ai/Apertus-70B-Instruct-2509" in model_ids
    assert "openai/gpt-oss-120b-Vsdo" in model_ids
    # No labels on the old binary → worker_group_id should be empty everywhere.
    assert all(e["worker_group_id"] == "" for e in out)


def test_upgraded_payload_groups_multinode_replica():
    """Simulated v0.0.6 deployment: the gemma 'multi-node demo' pair share a
    worker_group_id. One has a service, the other is metrics-only with id=''.
    Backend returns both entries with the shared worker_group_id so the
    frontend can aggregate them into one logical replica."""
    with patch("backend.services.model_service.requests.get") as mock_get:
        mock_get.return_value = type(
            "R",
            (),
            {"json": lambda self=None: _load_fixture("dnt_table_upgraded.json")},
        )()
        out = get_all_models("http://x/v1/dnt/table", with_details=True)
    # Find the shared-wg cluster
    by_wg: dict[str, list] = {}
    for e in out:
        by_wg.setdefault(e["worker_group_id"], []).append(e)
    multi = [v for v in by_wg.values() if len(v) > 1]
    assert multi, "fixture should contain at least one multi-peer worker group"
    # At least one peer in the multi-peer group should be metrics-only (id='').
    pair = multi[0]
    assert any(e["id"] == "" for e in pair), pair
    assert any(e["id"] != "" for e in pair), pair
