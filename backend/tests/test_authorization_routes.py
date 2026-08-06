"""Route-level tests for per-user model authorization: /v1/whoami, the
per-caller /v1/models* filtering, and the 403 permission_error envelope on
an inference route."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from testcontainers.postgres import PostgresContainer
from sqlmodel import SQLModel, Session, create_engine

from backend.models.entities import APIKey

ALICE = "alice@epfl.ch"
BOB = "bob@ethz.ch"
CAROL = "carol@unibas.ch"

ALICE_KEY = "sk-rc-alice-authz-test"
BOB_KEY = "sk-rc-bob-authz-test"
CAROL_KEY = "sk-rc-carol-authz-test"


@pytest.fixture(scope="module")
def postgres():
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


@pytest.fixture(scope="module")
def client(postgres):
    import os

    os.environ["DATABASE_URL"] = postgres.get_connection_url()

    # Reset cached settings so it picks up the new DATABASE_URL
    from backend.config import get_settings

    get_settings.cache_clear()

    import backend.main as main

    # backend.main may already be imported (test_app.py runs first) with its
    # settings frozen against a container that's gone by now — re-freeze so
    # the lifespan engine points at THIS module's database.
    main.settings = get_settings()

    engine = create_engine(main.settings.database_url)
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        session.add(APIKey(key=ALICE_KEY, owner_email=ALICE, budget=1000))
        session.add(APIKey(key=BOB_KEY, owner_email=BOB, budget=1000))
        session.add(APIKey(key=CAROL_KEY, owner_email=CAROL, budget=1000))
        session.commit()

    with TestClient(main.app) as c:
        yield c


@pytest.fixture(autouse=True)
def _clear_caches():
    """Identity and auth-map caches must not leak between tests."""
    from backend.services import authorization_service
    from backend.services.auth_service import _reset_email_cache_for_tests

    _reset_email_cache_for_tests()
    authorization_service._reset_cache_for_tests()
    yield
    _reset_email_cache_for_tests()
    authorization_service._reset_cache_for_tests()


def _bearer(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


# ── /v1/whoami ──────────────────────────────────────────────────────────────


def test_whoami_resolves_key_to_email(client):
    response = client.get("/v1/whoami", headers=_bearer(ALICE_KEY))
    assert response.status_code == 200
    assert response.json() == {"email": ALICE}


def test_whoami_unknown_key_401(client):
    response = client.get("/v1/whoami", headers=_bearer("sk-rc-does-not-exist"))
    assert response.status_code == 401
    assert response.json()["error"]["type"] == "authentication_error"


# ── /v1/models* filtering ───────────────────────────────────────────────────


def _peer_entry(model_id: str, auth_value: str | None, launched_by: str = "") -> dict:
    """A get_all_models-shaped entry; the filter reads labels.authorization
    and (for the namespace check) the top-level launched_by."""
    labels = {"worker_group_id": "wg-" + model_id}
    if auth_value is not None:
        labels["authorization"] = auth_value
    if launched_by:
        labels["launched_by"] = launched_by
    return {
        "id": model_id,
        "object": "model",
        "created": "0x",
        "owner": "0x",
        "has_service": True,
        "peer_id": "Qm" + model_id,
        "labels": labels,
        "authorization": auth_value or "",
        "launched_by": launched_by,
    }


def _fake_passthrough_settings():
    class S:
        cscs_l1_base_url = "https://l1/v1"
        cscs_l1_api_key = "k"
        rcp_base_url = ""
        rcp_api_key = ""

    return S()


def _list_models(client, monkeypatch, headers=None):
    """GET /v1/models against a fixed DNT: a public model, an unlabeled
    (pre-feature) model, one restricted to alice+bob (mixed case), and a
    passthrough provider advertising one synthetic entry."""
    from backend.routers import models as models_router
    from backend.services import passthrough_service

    passthrough_service._reset_cache_for_tests()
    entries = [
        _peer_entry("org/public-model", "public"),
        _peer_entry("org/legacy-model", None),
        _peer_entry("org/secret-model", f"{ALICE},Bob@ETHZ.ch"),
    ]
    monkeypatch.setattr(
        models_router, "get_all_models", lambda endpoint, with_details=False: entries
    )
    with (
        patch.object(
            passthrough_service,
            "get_settings",
            return_value=_fake_passthrough_settings(),
        ),
        patch.object(
            passthrough_service,
            "_fetch_model_ids",
            new=AsyncMock(return_value={"swiss-ai/Apertus-70B-Instruct-2509"}),
        ),
    ):
        response = client.get("/v1/models", headers=headers or {})
    passthrough_service._reset_cache_for_tests()
    return response


def test_models_anonymous_sees_public_unlabeled_and_passthrough(client, monkeypatch):
    response = _list_models(client, monkeypatch)
    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "list"
    assert {e["id"] for e in body["data"]} == {
        "org/public-model",
        "org/legacy-model",
        "swiss-ai/Apertus-70B-Instruct-2509",
    }


def test_models_owner_sees_their_restricted_model(client, monkeypatch):
    response = _list_models(client, monkeypatch, headers=_bearer(ALICE_KEY))
    assert response.status_code == 200
    assert "org/secret-model" in {e["id"] for e in response.json()["data"]}


def test_models_other_listed_user_sees_it_case_insensitively(client, monkeypatch):
    """Bob is listed as 'Bob@ETHZ.ch' but his key resolves to lowercase —
    the comparison must not care."""
    response = _list_models(client, monkeypatch, headers=_bearer(BOB_KEY))
    assert response.status_code == 200
    assert "org/secret-model" in {e["id"] for e in response.json()["data"]}


def test_models_non_listed_user_does_not_see_it(client, monkeypatch):
    response = _list_models(client, monkeypatch, headers=_bearer(CAROL_KEY))
    assert response.status_code == 200
    ids = {e["id"] for e in response.json()["data"]}
    assert "org/secret-model" not in ids
    assert "org/public-model" in ids


def test_models_invalid_bearer_401(client, monkeypatch):
    """A header that IS present must resolve — a typo'd key surfaces as
    401, not as a silently narrower list."""
    response = _list_models(client, monkeypatch, headers=_bearer("sk-rc-typo"))
    assert response.status_code == 401


def test_models_detailed_filters_the_same_way(client, monkeypatch):
    from backend.routers import models as models_router

    entries = [
        _peer_entry("org/public-model", "public"),
        _peer_entry("org/secret-model", ALICE),
    ]
    monkeypatch.setattr(
        models_router, "get_all_models", lambda endpoint, with_details=False: entries
    )
    response = client.get("/v1/models_detailed")
    assert response.status_code == 200
    assert {e["id"] for e in response.json()["data"]} == {"org/public-model"}


def test_models_hides_peers_serving_outside_their_own_namespace(client, monkeypatch):
    """A peer advertising alice's namespace from a job launched by bob is
    never listed. Alice's own peer and pre-namespacing ids are unaffected."""
    from backend.routers import models as models_router
    from backend.services import passthrough_service

    passthrough_service._reset_cache_for_tests()
    entries = [
        _peer_entry("alice/org/model", "public", launched_by="alice"),
        _peer_entry("alice/org/squatted", "public", launched_by="bob"),
        _peer_entry("org/legacy-model", "public", launched_by="bob"),
    ]
    monkeypatch.setattr(
        models_router, "get_all_models", lambda endpoint, with_details=False: entries
    )
    response = client.get("/v1/models")
    assert response.status_code == 200
    assert {e["id"] for e in response.json()["data"]} == {
        "alice/org/model",
        "org/legacy-model",
    }


# ── enforcement end to end ──────────────────────────────────────────────────


def test_chat_completions_403_uses_permission_error_envelope(client, monkeypatch):
    """A caller not on a model's authorization list gets the OpenAI
    permission_error envelope from /v1/chat/completions — and the request
    never reaches the upstream proxy."""
    from backend.routers import completions as completions_router
    from backend.services import authorization_service

    dnt = {
        "/QmSecret": {
            "id": "QmSecret",
            "labels": {"worker_group_id": "wg", "authorization": ALICE},
            "service": [
                {"name": "llm", "identity_group": ["model=org/secret-model"]},
            ],
        }
    }
    monkeypatch.setattr(
        authorization_service, "_fetch_dnt", AsyncMock(return_value=dnt)
    )

    async def never_proxied(**kwargs):
        raise AssertionError("proxy must not be reached on a 403")

    monkeypatch.setattr(completions_router, "llm_proxy", never_proxied)

    response = client.post(
        "/v1/chat/completions",
        headers=_bearer(CAROL_KEY),
        json={
            "model": "org/secret-model",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert response.status_code == 403
    body = response.json()
    assert "detail" not in body
    assert body["error"]["type"] == "permission_error"
    assert "org/secret-model" in body["error"]["message"]


def test_non_string_model_falls_through_not_500(client, monkeypatch):
    """Routes that pass the raw body value give the gate whatever JSON came
    in — a non-string model must behave like an unknown id (fall through to
    the upstream's own 4xx), not crash the lookup into a 500."""
    from backend.routers import embeddings as embeddings_router
    from backend.services import authorization_service

    dnt = {
        "/QmAny": {
            "id": "QmAny",
            "labels": {"worker_group_id": "wg"},
            "service": [
                {"name": "llm", "identity_group": ["model=org/public-model"]},
            ],
        }
    }
    monkeypatch.setattr(
        authorization_service, "_fetch_dnt", AsyncMock(return_value=dnt)
    )

    async def fake_proxy(**kwargs):
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=400, content={"error": "bad model"})

    monkeypatch.setattr(embeddings_router, "llm_proxy_embeddings", fake_proxy)

    for bad_model in ([], {}, ["org/x"]):
        response = client.post(
            "/v1/embeddings",
            headers=_bearer(ALICE_KEY),
            json={"model": bad_model, "input": "hi"},
        )
        assert response.status_code != 500


def test_rotation_immediately_revokes_identity_on_whoami(client):
    """Rotating a key must evict it from the identity cache: the old key was
    just used against /v1/whoami (cache warm), yet after rotation it gets 401
    — not the victim's email for another cache-TTL window."""
    from backend.config import get_settings
    from backend.services.auth_service import rotate_key

    engine = create_engine(get_settings().database_url)
    old_key = "sk-rc-dave-rotation-test"
    with Session(engine) as session:
        session.add(APIKey(key=old_key, owner_email="dave@epfl.ch", budget=1000))
        session.commit()

    assert client.get("/v1/whoami", headers=_bearer(old_key)).status_code == 200

    rotate_key(engine, old_key)

    response = client.get("/v1/whoami", headers=_bearer(old_key))
    assert response.status_code == 401


def test_chat_completions_authorized_user_passes_the_gate(client, monkeypatch):
    """Alice IS on the list — the request clears the authorization gate and
    reaches the (stubbed) proxy."""
    import types

    from backend.routers import completions as completions_router
    from backend.services import authorization_service

    dnt = {
        "/QmSecret": {
            "id": "QmSecret",
            "labels": {"worker_group_id": "wg", "authorization": ALICE},
            "service": [
                {"name": "llm", "identity_group": ["model=org/secret-model"]},
            ],
        }
    }
    monkeypatch.setattr(
        authorization_service, "_fetch_dnt", AsyncMock(return_value=dnt)
    )

    async def fake_proxy(*, endpoint, api_key, request):
        return types.SimpleNamespace(ok=True)

    monkeypatch.setattr(completions_router, "llm_proxy", fake_proxy)

    response = client.post(
        "/v1/chat/completions",
        headers=_bearer(ALICE_KEY),
        json={
            "model": "org/secret-model",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert response.status_code == 200
