"""Route-level tests for GET/PUT/DELETE /v1/model-access/{model_id}.

Driven against SQLite rather than a Postgres container: these exercise our
permission and conflict rules, not database behaviour, and keeping them
container-free means the part of this feature that decides who may change a
model's audience is covered even where Docker isn't available.
"""

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from backend.models.entities import APIKey, ModelAccessOverride  # noqa: F401
from backend.redis_cache import get_token_cache
from backend.errors import http_exception_handler
from backend.routers import model_access

ALICE = "alice@epfl.ch"
BOB = "bob@ethz.ch"
ADMIN = "admin@epfl.ch"

ALICE_KEY = "sk-rc-alice-access"
BOB_KEY = "sk-rc-bob-access"
ADMIN_KEY = "sk-rc-admin-access"

MODEL = "alice/swiss-ai/Apertus-8B"


def _peer(model_id, authorization, launch_id, owner):
    """One DNT entry as model_service hands it to the router."""
    return {
        "id": model_id,
        # The listing filter drops peers below MIN_USER_OTELA_VERSION, and the
        # router reads the same filtered list the models page does.
        "otela_version": "sai-v0.0.9",
        "labels": {
            "authorization": authorization,
            "launch_id": launch_id,
            "launched_by_email": owner,
            "launched_by": "alice",
        },
    }


@pytest.fixture
def app():
    # StaticPool + one shared connection: an in-memory SQLite database lives
    # inside its connection, and TestClient runs the app on another thread, so
    # the default pool would hand the request a second, empty database.
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(APIKey(key=ALICE_KEY, owner_email=ALICE, budget=1000))
        session.add(APIKey(key=BOB_KEY, owner_email=BOB, budget=1000))
        session.add(
            APIKey(key=ADMIN_KEY, owner_email=ADMIN, budget=1000, is_admin=True)
        )
        session.commit()

    application = FastAPI()
    application.include_router(model_access.router)
    # The same handler main.py registers. Without it these tests would assert
    # against `detail`, while production rewrites every error into the OpenAI
    # envelope — so a frontend reading the wrong field would pass here and
    # show blank errors to users.
    #
    # Imported from backend.errors, NOT backend.main: importing the app here
    # would freeze its settings against this module's (database-less)
    # environment at collection time, and the suites that configure a
    # database before importing the app would then boot against an empty URL.
    application.add_exception_handler(StarletteHTTPException, http_exception_handler)
    application.state.engine = engine
    return application


def _error_message(res):
    """The human-readable text out of the OpenAI error envelope."""
    return res.json()["error"]["message"]


@pytest.fixture(autouse=True)
def _clear_caches():
    from backend.services.monitoring_service import _owner_email_cache

    _owner_email_cache.clear()
    get_token_cache().clear_access_overrides()
    yield
    _owner_email_cache.clear()
    get_token_cache().clear_access_overrides()


def _with_dnt(entries):
    return patch.object(model_access, "get_all_models", return_value=entries)


def _get(client, key=ALICE_KEY, model=MODEL):
    return client.get(
        f"/v1/model-access/{model}", headers={"Authorization": f"Bearer {key}"}
    )


def _put(client, policy, key=ALICE_KEY, model=MODEL):
    return client.put(
        f"/v1/model-access/{model}",
        headers={"Authorization": f"Bearer {key}"},
        json={"authorization": policy},
    )


def _delete(client, key=ALICE_KEY, model=MODEL):
    return client.delete(
        f"/v1/model-access/{model}", headers={"Authorization": f"Bearer {key}"}
    )


# ── reading state ───────────────────────────────────────────────────────────


def test_owner_sees_state_and_may_edit(app):
    with TestClient(app) as client, _with_dnt([_peer(MODEL, "public", "L1", ALICE)]):
        body = _get(client).json()
    assert body["effective_authorization"] == "public"
    assert body["label_authorization"] == "public"
    assert body["is_overridden"] is False
    assert body["can_edit"] is True


def test_unknown_model_is_404(app):
    with TestClient(app) as client, _with_dnt([]):
        assert _get(client).status_code == 404


def test_missing_bearer_is_401(app):
    with TestClient(app) as client, _with_dnt([_peer(MODEL, "public", "L1", ALICE)]):
        assert client.get(f"/v1/model-access/{MODEL}").status_code == 401


def test_outsider_cannot_read_a_restricted_models_allowlist(app):
    """The settings name the people who may use the model, so a caller who
    isn't one of them must not be able to read them off this endpoint."""
    with TestClient(app) as client, _with_dnt([_peer(MODEL, ALICE, "L1", ALICE)]):
        assert _get(client, key=BOB_KEY).status_code == 404


def test_listed_user_may_read_but_not_edit(app):
    with (
        TestClient(app) as client,
        _with_dnt([_peer(MODEL, f"{ALICE},{BOB}", "L1", ALICE)]),
    ):
        body = _get(client, key=BOB_KEY).json()
    assert body["can_edit"] is False


# ── changing it ─────────────────────────────────────────────────────────────


def test_owner_can_restrict_a_public_model(app):
    with TestClient(app) as client, _with_dnt([_peer(MODEL, "public", "L1", ALICE)]):
        body = _put(client, f"{ALICE},{BOB}").json()
    assert body["effective_authorization"] == f"{ALICE},{BOB}"
    # The label is untouched, which is what Reset goes back to.
    assert body["label_authorization"] == "public"
    assert body["is_overridden"] is True


def test_reset_returns_to_the_launch_label(app):
    with TestClient(app) as client, _with_dnt([_peer(MODEL, "public", "L1", ALICE)]):
        _put(client, ALICE)
        body = _delete(client).json()
    assert body["is_overridden"] is False
    assert body["effective_authorization"] == "public"


def test_reset_is_idempotent(app):
    with TestClient(app) as client, _with_dnt([_peer(MODEL, "public", "L1", ALICE)]):
        assert _delete(client).status_code == 200
        assert _delete(client).status_code == 200


def test_non_owner_cannot_edit(app):
    with (
        TestClient(app) as client,
        _with_dnt([_peer(MODEL, f"{ALICE},{BOB}", "L1", ALICE)]),
    ):
        assert _put(client, "public", key=BOB_KEY).status_code == 403


def test_admin_can_edit_anyones_model(app):
    with TestClient(app) as client, _with_dnt([_peer(MODEL, "public", "L1", ALICE)]):
        assert _put(client, ADMIN, key=ADMIN_KEY).status_code == 200


def test_ownerless_launch_is_admin_only(app):
    """No launched_by_email — pre-feature SML, or a k8s-hosted model."""
    with TestClient(app) as client, _with_dnt([_peer(MODEL, "public", "L1", "")]):
        assert _put(client, ALICE, key=ALICE_KEY).status_code == 403
        assert _put(client, ALICE, key=ADMIN_KEY).status_code == 200


def test_invalid_policy_is_400(app):
    with TestClient(app) as client, _with_dnt([_peer(MODEL, "public", "L1", ALICE)]):
        assert _put(client, "not-an-email").status_code == 400
        assert _put(client, "").status_code == 400


def test_private_is_not_accepted_over_the_api(app):
    """'private' is an SML launch-time shorthand; here the concrete email is
    what a caller must give."""
    with TestClient(app) as client, _with_dnt([_peer(MODEL, "public", "L1", ALICE)]):
        assert _put(client, "private").status_code == 400


def test_launch_without_an_id_cannot_be_managed(app):
    """Nothing to attach an override to — say so rather than 500 or silently
    no-op."""
    with TestClient(app) as client, _with_dnt([_peer(MODEL, "public", "", ALICE)]):
        res = _put(client, ALICE)
    assert res.status_code == 409
    assert "launch id" in _error_message(res)


# ── several launches under one name ─────────────────────────────────────────


def test_change_applies_to_every_launch_of_the_name(app):
    """A partial write would leave the name served by launches that disagree,
    which the gateway refuses to route for everyone."""
    with (
        TestClient(app) as client,
        _with_dnt(
            [
                _peer(MODEL, "public", "L1", ALICE),
                _peer(MODEL, "public", "L2", ALICE),
            ]
        ),
    ):
        body = _put(client, ALICE).json()
    assert body["conflict"] is False
    assert body["effective_authorization"] == ALICE
    assert {le["override_policy"] for le in body["launches"]} == {ALICE}


def test_cannot_edit_when_another_user_owns_one_of_the_launches(app):
    """Editing only the launch you own is exactly how you'd break the name."""
    with (
        TestClient(app) as client,
        _with_dnt(
            [
                _peer(MODEL, "public", "L1", ALICE),
                _peer(MODEL, "public", "L2", BOB),
            ]
        ),
    ):
        assert _put(client, ALICE).status_code == 403


def test_conflicting_launches_are_reported(app):
    with (
        TestClient(app) as client,
        _with_dnt(
            [
                _peer(MODEL, "public", "L1", ALICE),
                _peer(MODEL, BOB, "L2", ALICE),
            ]
        ),
    ):
        body = _get(client).json()
    assert body["conflict"] is True
    assert body["effective_authorization"] is None


# ── regressions ─────────────────────────────────────────────────────────────


def _old_peer(model_id, authorization, launch_id, owner):
    """A launch the public listing filter hides (OpenTela below the minimum)
    but which is still running and still routable by id."""
    peer = _peer(model_id, authorization, launch_id, owner)
    peer["otela_version"] = "sai-v0.0.5"
    return peer


def test_change_reaches_launches_the_listing_filter_hides(app):
    """Regression: `platform_namespaced` is listing-only — an entry it hides
    is still routed, so the gateway still enforces its label. Managing access
    off the filtered list wrote an override to some of a name's launches and
    not others, which is exactly the disagreement that makes the name
    unroutable for EVERYONE."""
    with (
        TestClient(app) as client,
        _with_dnt(
            [
                _peer(MODEL, "public", "L1", ALICE),
                _old_peer(MODEL, "public", "L2", ALICE),
            ]
        ),
    ):
        body = _put(client, ALICE).json()
    assert {le["launch_id"] for le in body["launches"]} == {"L1", "L2"}
    assert {le["override_policy"] for le in body["launches"]} == {ALICE}
    assert body["conflict"] is False


def test_hidden_launch_owned_by_someone_else_blocks_the_edit(app):
    """The ownership guard has to see hidden launches too, or it can be
    stepped around by a launch the listing filter drops."""
    with (
        TestClient(app) as client,
        _with_dnt(
            [
                _peer(MODEL, "public", "L1", ALICE),
                _old_peer(MODEL, "public", "L2", BOB),
            ]
        ),
    ):
        assert _put(client, ALICE).status_code == 403


def test_error_bodies_use_the_openai_envelope(app):
    """Regression: the frontend reads error.message. main.py rewrites every
    HTTPException into that envelope, so a route whose message only lands in
    `detail` shows the user a blank error."""
    with TestClient(app) as client, _with_dnt([_peer(MODEL, "public", "L1", ALICE)]):
        res = _put(client, "not-an-email")
    assert res.status_code == 400
    assert "not an email address" in _error_message(res)


def test_a_failed_write_changes_nothing(app):
    """Regression: the per-launch commit loop could fail halfway and leave a
    model's launches disagreeing — the exact state this endpoint refuses to
    create. One transaction means a failure is a no-op."""
    from backend.services import model_access_service

    launches = [
        _peer(MODEL, "public", "L1", ALICE),
        _peer(MODEL, "public", "L2", ALICE),
    ]
    real_get = model_access_service.Session

    with TestClient(app) as client, _with_dnt(launches):
        # Establish a known good state first.
        _put(client, ALICE)
        assert _get(client).json()["effective_authorization"] == ALICE

        calls = {"n": 0}

        class _FailsOnSecondRow:
            """Blow up partway through the batch, after the first row was
            staged but before the commit."""

            def __init__(self, engine):
                self._session = real_get(engine)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                self._session.__exit__(*exc)
                return False

            def get(self, *a, **kw):
                calls["n"] += 1
                if calls["n"] > 1:
                    raise RuntimeError("database went away mid-batch")
                return self._session.get(*a, **kw)

            def add(self, *a, **kw):
                return self._session.add(*a, **kw)

            def commit(self):
                return self._session.commit()

        with patch.object(model_access_service, "Session", _FailsOnSecondRow):
            with pytest.raises(RuntimeError):
                _put(client, BOB)

        # Read the DATABASE, not the cache the failed write never invalidated:
        # a stale cache would otherwise hide a half-applied change for the
        # length of its TTL and make this test pass for the wrong reason.
        get_token_cache().clear_access_overrides()

        # The failed change left the previous policy intact on BOTH launches.
        body = _get(client).json()
    assert body["effective_authorization"] == ALICE
    assert body["conflict"] is False


def test_reset_clears_every_launch_atomically(app):
    with (
        TestClient(app) as client,
        _with_dnt(
            [
                _peer(MODEL, "public", "L1", ALICE),
                _peer(MODEL, ALICE, "L2", ALICE),
            ]
        ),
    ):
        _put(client, BOB)
        body = _delete(client).json()
    assert body["is_overridden"] is False
    # Back to the launch labels, which disagree — so the conflict the labels
    # always had reappears rather than being papered over.
    assert body["conflict"] is True


def test_passthrough_and_unknown_models_are_404_not_500(app):
    with TestClient(app) as client, _with_dnt([_peer(MODEL, "public", "L1", ALICE)]):
        res = client.get(
            "/v1/model-access/CSCS-Inference/meta/Llama",
            headers={"Authorization": f"Bearer {ALICE_KEY}"},
        )
    assert res.status_code == 404


def test_unknown_api_key_is_401(app):
    with TestClient(app) as client, _with_dnt([_peer(MODEL, "public", "L1", ALICE)]):
        assert _get(client, key="sk-rc-nope").status_code == 401


def test_setting_the_policy_it_already_has_is_not_a_conflict(app):
    """Canonicalisation has to make an override compare equal to a label
    meaning the same thing, or saving 'no change' would break the model."""
    with (
        TestClient(app) as client,
        _with_dnt([_peer(MODEL, f"{ALICE},{BOB}", "L1", ALICE)]),
    ):
        body = _put(client, f" {BOB.upper()} , {ALICE} , {BOB} ").json()
    assert body["conflict"] is False
    assert body["effective_authorization"] == f"{BOB},{ALICE}"


def test_put_hides_existence_from_someone_who_cannot_see_the_model(app):
    """GET answers 404 for an outsider, so PUT must too — a 403 here would
    confirm the model exists to exactly the people the listing hides it
    from."""
    with TestClient(app) as client, _with_dnt([_peer(MODEL, ALICE, "L1", ALICE)]):
        assert _put(client, BOB, key=BOB_KEY).status_code == 404
        assert _delete(client, key=BOB_KEY).status_code == 404


def test_a_visible_non_owner_still_gets_403_not_404(app):
    """Someone on the allowlist already knows the model exists, so hiding it
    from them would be noise rather than protection — they get the real
    reason instead."""
    with (
        TestClient(app) as client,
        _with_dnt([_peer(MODEL, f"{ALICE},{BOB}", "L1", ALICE)]),
    ):
        res = _put(client, BOB, key=BOB_KEY)
    assert res.status_code == 403
    assert "launched this model" in _error_message(res)


def test_the_blocking_launch_is_the_one_named(app):
    """With several launches, "you don't own it" and "it has no owner" can
    both be true of different ones. Naming the wrong one sends the user
    looking in the wrong place."""
    with (
        TestClient(app) as client,
        _with_dnt(
            [
                _peer(MODEL, "public", "L1", ALICE),
                _peer(MODEL, "public", "L2", ""),
            ]
        ),
    ):
        res = _put(client, ALICE)
    assert res.status_code == 403
    assert "without an owner label" in _error_message(res)


def test_conflicting_labels_report_no_single_launch_label(app):
    """Reset returns each launch to its OWN label, so when they differ there
    is no one label to name — the UI has to be told that rather than shown a
    plausible-looking wrong one."""
    with (
        TestClient(app) as client,
        _with_dnt(
            [
                _peer(MODEL, "public", "L1", ALICE),
                _peer(MODEL, ALICE, "L2", ALICE),
            ]
        ),
    ):
        body = _put(client, BOB).json()
    assert body["label_authorization"] is None
    assert body["is_overridden"] is True
