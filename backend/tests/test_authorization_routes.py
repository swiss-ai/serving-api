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

# Names as the IdP reports them, recorded on profile load. Carol has none —
# she has only ever used the API — so she reads as a name derived from her
# address instead.
ALICE_NAME = "Alice Example"
BOB_NAME = "Bob Bühler"
CAROL_DERIVED_NAME = "Carol"

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
        session.add(
            APIKey(key=ALICE_KEY, owner_email=ALICE, owner_name=ALICE_NAME, budget=1000)
        )
        session.add(
            APIKey(key=BOB_KEY, owner_email=BOB, owner_name=BOB_NAME, budget=1000)
        )
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


def _peer_entry(model_id: str, auth_value: str | None, owner: str = ALICE) -> dict:
    """A get_all_models-shaped entry; the filter reads labels.authorization.

    Ids are username-namespaced and the peer version is current, because
    /v1/models* runs model_service.platform_namespaced first — an
    un-namespaced id or an old peer is dropped before authorization is
    even consulted, which would make these cases vacuous.

    Carries ``launched_by_email`` like a real SML launch does, so the tests
    below can hold the route to never publishing it."""
    labels = {"worker_group_id": "wg-" + model_id}
    if auth_value is not None:
        labels["authorization"] = auth_value
    if owner:
        labels["launched_by_email"] = owner
    return {
        "id": model_id,
        "object": "model",
        "created": "0x",
        "owner": "0x",
        "has_service": True,
        "peer_id": "Qm" + model_id,
        "otela_version": "sai-v0.0.6",
        "labels": labels,
        "authorization": auth_value or "",
        "launched_by_email": owner,
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
        _peer_entry("alice/org/public-model", "public"),
        _peer_entry("alice/org/legacy-model", None),
        _peer_entry("alice/org/secret-model", f"{ALICE},Bob@ETHZ.ch"),
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
        "alice/org/public-model",
        "alice/org/legacy-model",
        # Passthrough entries are advertised under their provider prefix
        # and carry no authorization label, so they read as public.
        "CSCS-Inference/swiss-ai/Apertus-70B-Instruct-2509",
    }


def test_models_owner_sees_their_restricted_model(client, monkeypatch):
    response = _list_models(client, monkeypatch, headers=_bearer(ALICE_KEY))
    assert response.status_code == 200
    assert "alice/org/secret-model" in {e["id"] for e in response.json()["data"]}


def test_models_other_listed_user_sees_it_case_insensitively(client, monkeypatch):
    """Bob is listed as 'Bob@ETHZ.ch' but his key resolves to lowercase —
    the comparison must not care."""
    response = _list_models(client, monkeypatch, headers=_bearer(BOB_KEY))
    assert response.status_code == 200
    assert "alice/org/secret-model" in {e["id"] for e in response.json()["data"]}


def test_models_non_listed_user_does_not_see_it(client, monkeypatch):
    response = _list_models(client, monkeypatch, headers=_bearer(CAROL_KEY))
    assert response.status_code == 200
    ids = {e["id"] for e in response.json()["data"]}
    assert "alice/org/secret-model" not in ids
    assert "alice/org/public-model" in ids


def test_models_invalid_bearer_401(client, monkeypatch):
    """A header that IS present must resolve — a typo'd key surfaces as
    401, not as a silently narrower list."""
    response = _list_models(client, monkeypatch, headers=_bearer("sk-rc-typo"))
    assert response.status_code == 401


def test_models_detailed_filters_the_same_way(client, monkeypatch):
    from backend.routers import models as models_router

    entries = [
        _peer_entry("alice/org/public-model", "public"),
        _peer_entry("alice/org/secret-model", ALICE),
    ]
    monkeypatch.setattr(
        models_router, "get_all_models", lambda endpoint, with_details=False: entries
    )
    response = client.get("/v1/models_detailed")
    assert response.status_code == 200
    assert {e["id"] for e in response.json()["data"]} == {"alice/org/public-model"}


# ── what the catalogue is willing to say about people ───────────────────────


def _entry(response, model_id: str) -> dict:
    return next(e for e in response.json()["data"] if e["id"] == model_id)


def test_models_never_publish_an_email_address(client, monkeypatch):
    """The listing is anonymously readable, so an address anywhere in it is a
    harvestable one — which is why the translation happens server-side and
    not in the card that renders it.

    Asserted over the whole payload rather than field by field: a future
    label that carries an address has to fail this test too, since ``labels``
    is echoed to the frontend as an opaque dict."""
    response = _list_models(client, monkeypatch)
    assert response.status_code == 200
    assert "@" not in response.text

    entry = _entry(response, "alice/org/public-model")
    assert entry["launched_by_name"] == ALICE_NAME
    assert "launched_by_email" not in entry
    assert "launched_by_email" not in entry["labels"]


def test_models_credit_a_launcher_the_idp_never_named(client, monkeypatch):
    """No recorded name → derived from the local part. Still a guess at a
    name, but never a routable address: the domain is gone."""
    from backend.routers import models as models_router

    entries = [_peer_entry("carol/org/public-model", "public", owner=CAROL)]
    monkeypatch.setattr(
        models_router, "get_all_models", lambda endpoint, with_details=False: entries
    )
    response = client.get("/v1/models")
    assert response.status_code == 200
    assert _entry(response, "carol/org/public-model")["launched_by_name"] == (
        CAROL_DERIVED_NAME
    )


def test_models_render_the_allowlist_as_names(client, monkeypatch):
    """A restricted model's collaborators are only ever named, never
    addressed — to the people on the list included. Raw addresses live on
    /v1/model-access, which is gated to the owner and admins."""
    response = _list_models(client, monkeypatch, headers=_bearer(ALICE_KEY))
    assert response.status_code == 200
    entry = _entry(response, "alice/org/secret-model")
    assert entry["authorization"] == f"{ALICE_NAME}, {BOB_NAME}"


def test_models_report_public_uniformly(client, monkeypatch):
    """A missing label and an explicit "public" one are the same policy, and
    the badge logic reads this one field — so both report "public" rather
    than one of them reporting ""."""
    response = _list_models(client, monkeypatch)
    assert _entry(response, "alice/org/public-model")["authorization"] == "public"
    assert _entry(response, "alice/org/legacy-model")["authorization"] == "public"


def test_models_say_whether_the_viewer_may_manage_access(client, monkeypatch):
    """The card used to compare the viewer's address with the launcher's,
    which meant shipping both. The backend answers instead."""
    owner_view = _list_models(client, monkeypatch, headers=_bearer(ALICE_KEY))
    assert _entry(owner_view, "alice/org/public-model")["can_manage_access"] is True

    other_view = _list_models(client, monkeypatch, headers=_bearer(CAROL_KEY))
    assert _entry(other_view, "alice/org/public-model")["can_manage_access"] is False

    anonymous = _list_models(client, monkeypatch)
    assert _entry(anonymous, "alice/org/public-model")["can_manage_access"] is False


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
                {"name": "llm", "identity_group": ["model=alice/org/secret-model"]},
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
            "model": "alice/org/secret-model",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert response.status_code == 403
    body = response.json()
    assert "detail" not in body
    assert body["error"]["type"] == "permission_error"
    assert "alice/org/secret-model" in body["error"]["message"]


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
                {"name": "llm", "identity_group": ["model=alice/org/public-model"]},
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
                {"name": "llm", "identity_group": ["model=alice/org/secret-model"]},
            ],
        }
    }
    monkeypatch.setattr(
        authorization_service, "_fetch_dnt", AsyncMock(return_value=dnt)
    )

    async def fake_proxy(*, endpoint, api_key, request, provider_label=None):
        return types.SimpleNamespace(ok=True)

    monkeypatch.setattr(completions_router, "llm_proxy", fake_proxy)

    response = client.post(
        "/v1/chat/completions",
        headers=_bearer(ALICE_KEY),
        json={
            "model": "alice/org/secret-model",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert response.status_code == 200


# ── post-launch access changes, across the whole stack ──────────────────────


def _owned_peer(model_id: str, auth_value: str, launch_id: str, owner: str) -> dict:
    entry = _peer_entry(model_id, auth_value)
    entry["labels"]["launch_id"] = launch_id
    entry["labels"]["launched_by_email"] = owner
    entry["launch_id"] = launch_id
    entry["launched_by_email"] = owner
    return entry


def _patch_dnt(monkeypatch, entries):
    """Point BOTH readers at the same fixture: the models router reads it
    through get_all_models, the authorization service through the DNT table.
    They have to agree, or listing and enforcement disagree."""
    from backend.routers import model_access, models as models_router
    from backend.services import authorization_service

    # Both routers bind get_all_models into their own namespace, so each has
    # to be patched — patching the service module would reach neither.
    monkeypatch.setattr(models_router, "get_all_models", lambda *a, **k: list(entries))
    monkeypatch.setattr(model_access, "get_all_models", lambda *a, **k: list(entries))

    async def _fetch(*_a, **_k):
        return {
            f"/p{i}": {
                "id": e["peer_id"],
                "labels": e["labels"],
                "service": [{"name": "llm", "identity_group": [f"model={e['id']}"]}],
            }
            for i, e in enumerate(entries)
        }

    monkeypatch.setattr(authorization_service, "_fetch_dnt", _fetch)


MANAGED = "alice/org/managed-model"


def test_restricting_a_public_model_takes_effect_across_the_stack(client, monkeypatch):
    """The whole point of the feature, end to end and against a real database:
    a model launched public stops being listed for, and stops being usable by,
    everyone else — without relaunching it."""
    from backend.services import authorization_service
    from backend.services.model_access_service import clear_override

    entries = [_owned_peer(MANAGED, "public", "LAUNCH-A", ALICE)]
    _patch_dnt(monkeypatch, entries)

    def visible_to(key):
        res = client.get("/v1/models", headers=_bearer(key))
        return [m["id"] for m in res.json()["data"] if m["id"] == MANAGED]

    try:
        assert visible_to(BOB_KEY) == [MANAGED]

        res = client.put(
            f"/v1/model-access/{MANAGED}",
            headers=_bearer(ALICE_KEY),
            json={"authorization": ALICE},
        )
        assert res.status_code == 200
        # The label is untouched — that is what Reset goes back to.
        assert res.json()["label_authorization"] == "public"

        authorization_service._reset_cache_for_tests()
        assert visible_to(BOB_KEY) == []
        assert visible_to(ALICE_KEY) == [MANAGED]

        res = client.post(
            "/v1/chat/completions",
            headers=_bearer(BOB_KEY),
            json={"model": MANAGED, "messages": [{"role": "user", "content": "hi"}]},
        )
        assert res.status_code == 403
        assert res.json()["error"]["type"] == "permission_error"
    finally:
        clear_override(client.app.state.engine, "LAUNCH-A")


def test_reset_restores_the_launch_label_across_the_stack(client, monkeypatch):
    from backend.services import authorization_service
    from backend.services.model_access_service import clear_override

    entries = [_owned_peer(MANAGED, "public", "LAUNCH-B", ALICE)]
    _patch_dnt(monkeypatch, entries)

    try:
        client.put(
            f"/v1/model-access/{MANAGED}",
            headers=_bearer(ALICE_KEY),
            json={"authorization": ALICE},
        )
        res = client.delete(f"/v1/model-access/{MANAGED}", headers=_bearer(ALICE_KEY))
        assert res.status_code == 200
        assert res.json()["is_overridden"] is False
        assert res.json()["effective_authorization"] == "public"

        authorization_service._reset_cache_for_tests()
        listed = client.get("/v1/models", headers=_bearer(BOB_KEY)).json()["data"]
        assert MANAGED in [m["id"] for m in listed]
    finally:
        clear_override(client.app.state.engine, "LAUNCH-B")


def test_listing_reports_the_override_translated_to_names(client, monkeypatch):
    """The listing reports the EFFECTIVE policy, so the card's "Restricted"
    badge is right about a model restricted after launch — and reports it as
    names, so the collaborator list an owner just typed in does not become a
    public one."""
    from backend.services import authorization_service
    from backend.services.model_access_service import clear_override

    entries = [_owned_peer(MANAGED, "public", "LAUNCH-D", ALICE)]
    _patch_dnt(monkeypatch, entries)

    try:
        res = client.put(
            f"/v1/model-access/{MANAGED}",
            headers=_bearer(ALICE_KEY),
            json={"authorization": f"{ALICE},{CAROL}"},
        )
        assert res.status_code == 200

        authorization_service._reset_cache_for_tests()
        listed = client.get("/v1/models", headers=_bearer(ALICE_KEY))
        entry = _entry(listed, MANAGED)
        assert entry["authorization"] == f"{ALICE_NAME}, {CAROL_DERIVED_NAME}"
        assert "@" not in listed.text
    finally:
        clear_override(client.app.state.engine, "LAUNCH-D")


def test_an_override_outlives_the_cache_not_the_database(client, monkeypatch):
    """A restriction has to be durable: it is a row, not a cache entry, so
    flushing every cache must not quietly re-open the model."""
    from backend.redis_cache import get_token_cache
    from backend.services import authorization_service
    from backend.services.model_access_service import clear_override

    entries = [_owned_peer(MANAGED, "public", "LAUNCH-C", ALICE)]
    _patch_dnt(monkeypatch, entries)

    try:
        client.put(
            f"/v1/model-access/{MANAGED}",
            headers=_bearer(ALICE_KEY),
            json={"authorization": ALICE},
        )
        get_token_cache().clear_cache()
        authorization_service._reset_cache_for_tests()

        listed = client.get("/v1/models", headers=_bearer(BOB_KEY)).json()["data"]
        assert MANAGED not in [m["id"] for m in listed]
    finally:
        clear_override(client.app.state.engine, "LAUNCH-C")
