"""Post-launch access overrides: the policy grammar accepted from the UI,
the launch-scoped storage, and the permission rule for who may edit.

The overlay only earns its place if it is safe in the two ways labels are
not: a change must reach every replica promptly, and a change must never
outlive the job it was made for."""

import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlmodel import SQLModel, create_engine

# Imported for its side effect: it registers the table on SQLModel.metadata,
# which is what create_all below builds from.
from backend.models.entities import ModelAccessOverride  # noqa: F401
from backend.redis_cache import get_token_cache
from backend.services import model_access_service
from backend.services.model_access_service import (
    InvalidPolicyError,
    clear_override,
    get_override,
    load_overrides,
    may_edit,
    normalize_policy_value,
    prune_dead_overrides,
    set_override,
)


@pytest.fixture
def engine():
    """In-memory SQLite: this module tests our own logic over the table, not
    Postgres behaviour, so it needs no container."""
    eng = create_engine("sqlite://")
    SQLModel.metadata.create_all(eng)
    return eng


@pytest.fixture(autouse=True)
def _clear_cache():
    cache = get_token_cache()
    cache.clear_access_overrides()
    # The pruning grace clock is cache state too: a mark left behind would
    # let the next test delete a row on what should be its first sighting.
    cache.clear_missing_launches()
    yield
    cache.clear_access_overrides()
    cache.clear_missing_launches()


def _after(seconds):
    """Run the body as if ``seconds`` had passed, by moving the service's
    clock rather than sleeping through a day-long grace window."""
    return patch.object(
        model_access_service,
        "time",
        SimpleNamespace(time=lambda: time.time() + seconds),
    )


_PAST_GRACE = model_access_service._PRUNE_GRACE_SECONDS + 1


# ── policy grammar ──────────────────────────────────────────────────────────


def test_public_is_accepted_in_any_case():
    for value in ("public", "PUBLIC", "  Public "):
        assert normalize_policy_value(value) == "public"


def test_email_list_is_canonicalized():
    """Stripped, lowercased, de-duplicated, order preserved — so an override
    saying the same thing as a label compares equal to it and doesn't read as
    a conflict between replicas."""
    assert (
        normalize_policy_value(" B@ETHZ.ch , a@epfl.ch ,b@ethz.ch")
        == "b@ethz.ch,a@epfl.ch"
    )


@pytest.mark.parametrize(
    "value", ["", "   ", "not-an-email", "a@epfl.ch,nope", "public,a@epfl.ch", ","]
)
def test_invalid_policies_are_rejected(value):
    with pytest.raises(InvalidPolicyError):
        normalize_policy_value(value)


def test_private_is_not_a_policy_here():
    """'private' is an SML launch-time shorthand resolved against whoami.
    Once a model exists there is a concrete email to store instead, so the
    word must not silently become an allowlist of one literal string."""
    with pytest.raises(InvalidPolicyError):
        normalize_policy_value("private")


# ── storage, keyed by launch ────────────────────────────────────────────────


def test_set_and_load_override(engine):
    set_override(engine, "L1", "swiss-ai/M", "a@epfl.ch", "public", "a@epfl.ch")
    assert load_overrides(engine) == {"L1": "public"}


def test_set_override_replaces_not_duplicates(engine):
    set_override(engine, "L1", "swiss-ai/M", "a@epfl.ch", "public", "a@epfl.ch")
    set_override(engine, "L1", "swiss-ai/M", "a@epfl.ch", "b@ethz.ch", "a@epfl.ch")
    assert load_overrides(engine) == {"L1": "b@ethz.ch"}
    row = get_override(engine, "L1")
    assert row.policy == "b@ethz.ch"
    assert row.updated_at >= row.created_at


def test_clear_override_is_reset_and_is_idempotent(engine):
    set_override(engine, "L1", "swiss-ai/M", "a@epfl.ch", "public", "a@epfl.ch")
    assert clear_override(engine, "L1") is True
    assert load_overrides(engine) == {}
    # The Reset button must be safe to press twice.
    assert clear_override(engine, "L1") is False


def test_overrides_of_different_launches_are_independent(engine):
    """Two launches of the SAME name hold separate rows, so overriding one
    cannot silently rewrite the other's policy."""
    set_override(engine, "L1", "swiss-ai/M", "a@epfl.ch", "public", "a@epfl.ch")
    set_override(engine, "L2", "swiss-ai/M", "b@ethz.ch", "b@ethz.ch", "b@ethz.ch")
    assert load_overrides(engine) == {"L1": "public", "L2": "b@ethz.ch"}


def test_a_relaunch_under_the_same_name_starts_clean(engine):
    """The point of keying on launch_id: a job ends, someone launches the
    same NAME again, and the dead override must not attach to it."""
    set_override(
        engine, "old-launch", "swiss-ai/M", "a@epfl.ch", "a@epfl.ch", "a@epfl.ch"
    )
    overrides = load_overrides(engine)
    # The new launch presents its own id, which nothing has an override for.
    assert "fresh-launch" not in overrides


def _two_overrides(engine):
    set_override(engine, "live", "swiss-ai/M", "a@epfl.ch", "public", "a@epfl.ch")
    set_override(engine, "gone", "swiss-ai/N", "a@epfl.ch", "a@epfl.ch", "a@epfl.ch")


def test_one_absence_only_marks_it(engine):
    """A single DNT read is never enough. It may be partial — a heartbeat
    gap, a peer mid-restart — and deleting on it would revert a restricted
    model to its launch label, i.e. quietly republish it."""
    _two_overrides(engine)
    assert prune_dead_overrides(engine, {"live"}) == 0
    assert load_overrides(engine) == {"live": "public", "gone": "a@epfl.ch"}


def test_prune_drops_a_launch_absent_for_the_whole_window(engine):
    """Still housekeeping, just patient: once the launch has been gone for
    the whole grace window the row is reclaimed."""
    _two_overrides(engine)
    assert prune_dead_overrides(engine, {"live"}) == 0
    with _after(_PAST_GRACE):
        assert prune_dead_overrides(engine, {"live"}) == 1
    assert load_overrides(engine) == {"live": "public"}


def test_reappearing_restarts_the_clock(engine):
    """The case the window exists for: a launch missing from one read and
    back on the next must survive, however long the run of reads is."""
    _two_overrides(engine)
    assert prune_dead_overrides(engine, {"live"}) == 0
    assert prune_dead_overrides(engine, {"live", "gone"}) == 0
    with _after(_PAST_GRACE):
        assert prune_dead_overrides(engine, {"live"}) == 0
    assert load_overrides(engine) == {"live": "public", "gone": "a@epfl.ch"}


def test_an_unreadable_grace_clock_never_prunes(engine):
    """Losing the clock (a flush, an expiry, a Redis blip) restarts the
    window rather than deleting against a map we could not read — it can
    only ever delay a delete."""
    _two_overrides(engine)
    assert prune_dead_overrides(engine, {"live"}) == 0
    get_token_cache().clear_missing_launches()
    with _after(_PAST_GRACE):
        assert prune_dead_overrides(engine, {"live"}) == 0
    assert "gone" in load_overrides(engine)


def test_an_empty_mesh_read_still_prunes_nothing(engine):
    """`get_all_models` returns [] when the DNT read fails, so an empty live
    set means "we couldn't see the mesh", never "nothing is running"."""
    _two_overrides(engine)
    with _after(_PAST_GRACE):
        assert prune_dead_overrides(engine, set()) == 0
    assert load_overrides(engine) == {"live": "public", "gone": "a@epfl.ch"}


def test_the_grace_clock_does_not_outgrow_the_table(engine):
    """The mark map is rebuilt from rows that are still both present and
    missing, so bookkeeping meant to bound table growth cannot itself grow
    without bound."""
    _two_overrides(engine)
    prune_dead_overrides(engine, {"live"})
    assert set(get_token_cache().get_missing_launches()) == {"gone"}

    # Once the row is gone, so is its mark.
    with _after(_PAST_GRACE):
        prune_dead_overrides(engine, {"live"})
    assert get_token_cache().get_missing_launches() == {}


# ── cache coherence ─────────────────────────────────────────────────────────


def test_writes_invalidate_the_cache(engine):
    """An access change has to be visible on the next request, on every
    replica — that is the whole reason it isn't a label."""
    assert load_overrides(engine) == {}  # populates the cache with "nothing"
    set_override(engine, "L1", "swiss-ai/M", "a@epfl.ch", "public", "a@epfl.ch")
    assert load_overrides(engine) == {"L1": "public"}

    clear_override(engine, "L1")
    assert load_overrides(engine) == {}


def test_no_engine_means_no_overrides():
    """Fixture-mode dev and unit tests run without a database; that must read
    as "enforce the labels", not as an error on the inference path."""
    assert load_overrides(None) == {}


def test_database_failure_falls_back_to_labels(engine, caplog):
    """A DB blip must not take enforcement with it, and must not open
    anything up: dropping to "no overrides" can only ever revert a model to
    what its launcher originally asked for."""
    broken = create_engine("sqlite://")  # no tables created
    with caplog.at_level("WARNING", logger="backend"):
        assert load_overrides(broken) == {}
    assert any("overrides" in r.message for r in caplog.records)


# ── who may edit ────────────────────────────────────────────────────────────


def test_owner_may_edit_case_insensitively():
    assert may_edit("A@epfl.ch", "a@epfl.ch", False) is True


def test_other_users_may_not_edit():
    assert may_edit("b@ethz.ch", "a@epfl.ch", False) is False


def test_admin_may_edit_anything():
    assert may_edit("admin@epfl.ch", "a@epfl.ch", True) is True
    assert may_edit("admin@epfl.ch", "", True) is True


def test_ownerless_launch_is_admin_only():
    """No launched_by_email (pre-feature SML, or our k8s models) means there
    is nobody to attribute the launch to — which must read as "nobody but an
    admin", not as "anybody"."""
    assert may_edit("a@epfl.ch", "", False) is False
    assert may_edit(None, "", False) is False


def test_anonymous_may_not_edit():
    assert may_edit(None, "a@epfl.ch", False) is False


def test_prune_no_ops_on_an_empty_live_set(engine):
    """get_all_models returns [] when the DNT read fails. Pruning on that
    would delete every override during an outage and silently revert every
    model to its launch label — so an empty set means "we couldn't see the
    mesh", never "nothing is running"."""
    set_override(engine, "L1", "swiss-ai/M", "a@epfl.ch", "public", "a@epfl.ch")
    assert prune_dead_overrides(engine, set()) == 0
    assert load_overrides(engine) == {"L1": "public"}
