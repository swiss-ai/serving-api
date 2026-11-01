"""The caches' cross-replica behaviour, exercised against a REAL Redis.

Every other test in this suite runs with Redis unreachable, so it silently
exercises RedisTokenCache's per-process fallback — which is the branch prod
never takes. These tests skip without a server and are the only coverage of
the claim the Redis work was done for: that two serving-api replicas share
one view of identity, of the authorization map, and of access overrides.

Point them at a server with REDIS_HOST (docker/podman run -p 6379:6379 redis).
"""

import functools
import os
import socket

import pytest

from backend.redis_cache import RedisTokenCache

HOST = os.environ.get("REDIS_HOST", "localhost")
PORT = 6379


@functools.lru_cache(maxsize=1)
def _redis_reachable() -> bool:
    """Probed once with a short timeout, rather than by constructing a client
    per test: RedisTokenCache's own connect retries for seconds before giving
    up, which would make skipping this module cost more than running it."""
    try:
        with socket.create_connection((HOST, PORT), timeout=0.25):
            return True
    except OSError:
        return False


def _cache():
    """A cache instance standing in for one serving-api replica."""
    return RedisTokenCache(host=HOST, port=PORT)


@pytest.fixture
def replicas():
    if not _redis_reachable():
        pytest.skip(f"no Redis at {HOST}:{PORT}; start one to run these")
    a, b = _cache(), _cache()
    a.clear_cache()
    yield a, b
    a.clear_cache()


# ── identity ────────────────────────────────────────────────────────────────


def test_identity_written_by_one_replica_is_read_by_another(replicas):
    a, b = replicas
    a.set_email("sk-rc-x", "alice@epfl.ch", ttl=60)
    assert b.get_email("sk-rc-x") == "alice@epfl.ch"


def test_rotation_evicts_identity_on_every_replica(replicas):
    """The reason identity moved to Redis: a key rotated on one replica must
    stop resolving on the others immediately, not after their own TTL."""
    a, b = replicas
    a.set_email("sk-rc-x", "alice@epfl.ch", ttl=60)
    assert b.get_email("sk-rc-x") == "alice@epfl.ch"

    a.remove_email("sk-rc-x")
    assert b.get_email("sk-rc-x") is None


# ── authorization map ───────────────────────────────────────────────────────


VIEW = {"entries": {"m": [["a@epfl.ch", "L1"]]}, "owners": {"L1": "a@epfl.ch"}}


def test_a_replica_that_never_fetched_reads_the_shared_map(replicas):
    """The point of sharing: a replica that just booted enforces from a peer's
    map instead of paying its own cold-start fail-open."""
    a, b = replicas
    a.set_auth_map(VIEW, ttl=60)
    a.mark_auth_map_fetched(ttl=60)

    assert b.get_auth_map() == VIEW
    assert b.auth_map_is_fresh() is True


def test_the_map_survives_the_json_round_trip_unchanged(replicas):
    """Redis stores JSON while the fallback stores the object itself, so a
    shape that doesn't round-trip (a tuple, a set) would behave differently
    in prod than in every other test. Lists must come back as lists."""
    a, b = replicas
    a.set_auth_map(VIEW, ttl=60)
    got = b.get_auth_map()
    assert got == VIEW
    assert isinstance(got["entries"]["m"][0], list)


def test_freshness_expires_while_the_stale_map_survives(replicas):
    """The two-key split: the sentinel is short so refreshes happen, the data
    is long so an outage enforces stale rather than failing open."""
    a, b = replicas
    a.set_auth_map(VIEW, ttl=60)
    a.mark_auth_map_fetched(ttl=1)

    a.invalidate_auth_map_freshness()
    assert b.auth_map_is_fresh() is False
    assert b.get_auth_map() == VIEW


def test_zero_ttl_drops_the_map_instead_of_erroring(replicas):
    """Redis rejects a non-positive expiry outright, so without the explicit
    guard a zero TTL would log an error and leave the PREVIOUS map in place —
    the opposite of what the caller asked for."""
    a, b = replicas
    a.set_auth_map(VIEW, ttl=60)
    a.set_auth_map({"entries": {}, "owners": {}}, ttl=0)
    assert b.get_auth_map() is None


# ── access overrides ────────────────────────────────────────────────────────


def test_an_access_change_is_visible_to_other_replicas(replicas):
    a, b = replicas
    a.set_access_overrides({"L1": "a@epfl.ch"}, ttl=60)
    assert b.get_access_overrides() == {"L1": "a@epfl.ch"}


def test_a_write_invalidates_every_replicas_view(replicas):
    """Writes clear the shared key, which is what makes an access change take
    effect on the next request everywhere rather than after each replica's
    own TTL."""
    a, b = replicas
    a.set_access_overrides({"L1": "a@epfl.ch"}, ttl=60)
    assert b.get_access_overrides() is not None

    a.clear_access_overrides()
    assert b.get_access_overrides() is None


def test_an_empty_override_map_is_cached_not_mistaken_for_a_miss(replicas):
    """ "No overrides" is a real answer worth caching. If it read back as a
    miss, every request would re-query Postgres for the common case."""
    a, b = replicas
    a.set_access_overrides({}, ttl=60)
    assert b.get_access_overrides() == {}


# ── namespaces don't collide ────────────────────────────────────────────────


def test_the_three_caches_are_independent(replicas):
    """They share a client but not a key space: clearing one must not log
    users out or drop the authorization map."""
    a, b = replicas
    a.add_token("sk-rc-x", ttl=60)
    a.set_email("sk-rc-x", "alice@epfl.ch", ttl=60)
    a.set_auth_map(VIEW, ttl=60)
    a.set_access_overrides({"L1": "public"}, ttl=60)

    a.clear_access_overrides()
    assert b.get_email("sk-rc-x") == "alice@epfl.ch"
    assert b.get_auth_map() == VIEW
    assert b.has_token("sk-rc-x") is True

    a.clear_auth_map()
    assert b.get_email("sk-rc-x") == "alice@epfl.ch"
    assert b.has_token("sk-rc-x") is True
