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
        "served_model_name": "swiss-ai/Apertus-8B",
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
    """A peer with no advertised `service` (multi-node follower, or a head
    still in PENDING during boot) should fall back to its served_model_name
    label so the frontend can render the model card during the brief window
    before the service is published. Without the fallback, the peer's id
    stays empty and the frontend silently drops it."""
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
    # Follower inherits id from the served_model_name label — same model card.
    assert by_id["QmFollower"]["id"] == "swiss-ai/Apertus-8B"
    assert by_id["QmFollower"]["status"] == "pending"
    # Shared worker_group_id lets the frontend group them within the model.
    assert (
        by_id["QmHead"]["worker_group_id"]
        == by_id["QmFollower"]["worker_group_id"]
        == "12345"
    )


def test_pending_peer_without_served_model_name_label_falls_back_to_empty_id():
    """Defensive: if a peer is mid-boot from an older binary that doesn't
    emit served_model_name, we still surface it via worker_group_id with
    id=''. The frontend then needs another peer in the same group with an
    id to attribute it; otherwise it's dropped."""
    peer = {
        **PEER_NEW_BINARY_FOLLOWER,
        "labels": {
            k: v
            for k, v in PEER_NEW_BINARY_FOLLOWER["labels"].items()
            if k != "served_model_name"
        },
    }
    with patch("backend.services.model_service.requests.get") as mock_get:
        mock_get.return_value = _dnt_response({"/QmPending": peer})
        out = get_all_models("http://x/v1/dnt/table", with_details=True)
    assert len(out) == 1
    assert out[0]["id"] == ""
    assert out[0]["worker_group_id"] == "12345"


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


# ── /v1/models L1 merge ─────────────────────────────────────────────────────


def _fake_l1_settings(base_url="https://l1/v1", api_key="k"):
    class S:
        cscs_l1_base_url = base_url
        cscs_l1_api_key = api_key

    return S()


def _patch_l1_fetch(ids):
    from unittest.mock import AsyncMock
    from backend.services import cscs_l1_service

    return patch.object(
        cscs_l1_service,
        "_fetch_l1_model_ids",
        new=AsyncMock(return_value=set(ids)),
    )


def test_models_router_merges_l1_entries():
    """The models router should advertise L1-hosted models on top of the
    OpenTela DNT table so the frontend's model list includes them."""
    import asyncio

    from backend.routers.models import _with_l1
    from backend.services import cscs_l1_service

    cscs_l1_service._reset_cache_for_tests()
    base = [{"id": "some/local-model", "object": "model"}]
    with (
        patch.object(cscs_l1_service, "get_settings", return_value=_fake_l1_settings()),
        _patch_l1_fetch(["Apertus-8B-Instruct-2509", "Apertus-70B-Instruct-2509"]),
    ):
        merged = asyncio.run(_with_l1(list(base), with_details=True))
    ids = {e["id"] for e in merged}
    assert "some/local-model" in ids
    assert "Apertus-8B-Instruct-2509" in ids
    assert "Apertus-70B-Instruct-2509" in ids


def test_models_router_dedupes_l1_against_dnt():
    """If a model is already advertised by OpenTela (e.g. mid-migration
    we still have a k8s replica running), don't double-list it from L1.
    The DNT entry wins — that's the one carrying real peer metadata."""
    import asyncio

    from backend.routers.models import _with_l1
    from backend.services import cscs_l1_service

    cscs_l1_service._reset_cache_for_tests()
    base = [{"id": "Apertus-8B-Instruct-2509", "launched_by": "rosmith"}]
    with (
        patch.object(cscs_l1_service, "get_settings", return_value=_fake_l1_settings()),
        _patch_l1_fetch(["Apertus-8B-Instruct-2509"]),
    ):
        merged = asyncio.run(_with_l1(list(base), with_details=True))
    apertus_8b = [e for e in merged if e["id"] == "Apertus-8B-Instruct-2509"]
    assert len(apertus_8b) == 1
    assert apertus_8b[0]["launched_by"] == "rosmith"  # DNT entry kept


def test_models_router_skips_l1_when_unconfigured():
    """No env → L1 entries withheld so we don't expose models we can't
    actually proxy."""
    import asyncio

    from backend.routers.models import _with_l1
    from backend.services import cscs_l1_service

    cscs_l1_service._reset_cache_for_tests()
    base = [{"id": "some/local-model", "object": "model"}]
    with patch.object(
        cscs_l1_service,
        "get_settings",
        return_value=_fake_l1_settings(base_url="", api_key=""),
    ):
        merged = asyncio.run(_with_l1(list(base), with_details=True))
    assert merged == base


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
    worker_group_id. Both peers carry the served_model_name label, so both
    resolve to the same model id even though only one advertises a service.
    Backend returns both entries with the shared worker_group_id + model id
    so the frontend can aggregate them into one logical replica."""
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
    pair = multi[0]
    # Both peers in the group share the same non-empty model id.
    ids = {e["id"] for e in pair}
    assert ids != {""}, pair
    assert len(ids) == 1, f"peers in one worker group should share one model id: {ids}"
