import json
import os
import redis
import logging
import time

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class RedisTokenCache:
    def __init__(
        self, host: str = None, port: int = 6379, db: int = 0, password: str = None
    ):
        """
        Initialize Redis connection for token caching

        Args:
            host: Redis host (defaults to REDIS_HOST env var or localhost)
            port: Redis port (defaults to 6379)
            db: Redis database number (defaults to 0)
            password: Redis password (defaults to REDIS_PASSWORD env var)
        """
        self.host = host or os.environ.get("REDIS_HOST", "localhost")
        self.port = port
        self.db = db
        self.password = password or os.environ.get("REDIS_PASSWORD")

        try:
            self.redis_client = redis.Redis(
                host=self.host,
                port=self.port,
                db=self.db,
                password=self.password,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
                retry_on_timeout=True,
                health_check_interval=30,
            )
            # Test connection
            self.redis_client.ping()
            logger.debug(f"Connected to Redis at {self.host}:{self.port}")
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
            # Fallback to in-memory for development/testing. Per-process, so a
            # multi-replica deployment that loses Redis also loses cross-replica
            # consistency until it comes back — same trade the token cache makes.
            self.redis_client = None
            self._fallback_cache = set()
            self._fallback_emails = {}
            self._fallback_auth_map = None
            self._fallback_auth_map_expires_at = 0.0
            self._fallback_auth_map_fresh_until = 0.0
            self._fallback_overrides = None
            self._fallback_overrides_expires_at = 0.0
            self._fallback_missing = None
            self._fallback_missing_expires_at = 0.0

    def add_token(self, token: str, ttl: int = 3600) -> bool:
        """
        Add a token to the cache with optional TTL

        Args:
            token: The token to cache
            ttl: Time to live in seconds (default: 1 hour)

        Returns:
            True if successful, False otherwise
        """
        try:
            if self.redis_client:
                return self.redis_client.setex(f"token:{token}", ttl, "valid")
            else:
                # Fallback to in-memory
                self._fallback_cache.add(token)
                return True
        except Exception as e:
            logger.error(f"Error adding token to cache: {e}")
            return False

    def has_token(self, token: str) -> bool:
        """
        Check if a token exists in the cache

        Args:
            token: The token to check

        Returns:
            True if token exists, False otherwise
        """
        try:
            if self.redis_client:
                return self.redis_client.exists(f"token:{token}") > 0
            else:
                return token in self._fallback_cache
        except Exception as e:
            logger.error(f"Error checking token in cache: {e}")
            return False

    def remove_token(self, token: str) -> bool:
        """
        Remove a token from the cache

        Args:
            token: The token to remove

        Returns:
            True if successful, False otherwise
        """
        try:
            if self.redis_client:
                return self.redis_client.delete(f"token:{token}") > 0
            else:
                # Fallback to in-memory
                if token in self._fallback_cache:
                    self._fallback_cache.remove(token)
                    return True
                return False
        except Exception as e:
            logger.error(f"Error removing token from cache: {e}")
            return False

    def set_email(self, token: str, email: str, ttl: int = 300) -> bool:
        """
        Cache the owner email an API key resolves to.

        Shared, unlike a per-process dict: prod runs several serving-api
        replicas, and identity has to be revoked on all of them at once when a
        key is rotated (see remove_email).

        Args:
            token: The API key
            email: The owner email it resolves to
            ttl: Time to live in seconds (default: 5 minutes)

        Returns:
            True if successful, False otherwise
        """
        try:
            if self.redis_client:
                return bool(self.redis_client.setex(f"email:{token}", ttl, email))
            else:
                self._fallback_emails[token] = email
                return True
        except Exception as e:
            logger.error(f"Error caching email for token: {e}")
            return False

    def get_email(self, token: str) -> str | None:
        """
        Look up the cached owner email for an API key.

        Returns:
            The email, or None when not cached (or on any Redis error, so a
            cache failure becomes a DB read rather than a failed request).
        """
        try:
            if self.redis_client:
                return self.redis_client.get(f"email:{token}")
            else:
                return self._fallback_emails.get(token)
        except Exception as e:
            logger.error(f"Error reading cached email for token: {e}")
            return None

    def remove_email(self, token: str) -> bool:
        """
        Drop a key's cached identity — every replica's, since the cache is
        shared. This is what makes rotation take effect immediately rather
        than after the TTL on whichever replicas didn't serve the rotation.

        Returns:
            True if an entry was removed, False otherwise
        """
        try:
            if self.redis_client:
                return self.redis_client.delete(f"email:{token}") > 0
            else:
                return self._fallback_emails.pop(token, None) is not None
        except Exception as e:
            logger.error(f"Error removing cached email for token: {e}")
            return False

    def clear_emails(self) -> bool:
        """
        Clear cached identities only, leaving token validity untouched.

        Returns:
            True if successful, False otherwise
        """
        try:
            if self.redis_client:
                keys = self.redis_client.keys("email:*")
                if keys:
                    self.redis_client.delete(*keys)
                return True
            else:
                self._fallback_emails.clear()
                return True
        except Exception as e:
            logger.error(f"Error clearing cached emails: {e}")
            return False

    # ── DNT-derived model → authorization map ────────────────────────────
    #
    # The key carries a shape version. A rolling deploy runs old and new
    # pods against one Redis, and a map written in the old shape would be
    # read as garbage by the new code (and vice versa); separate keys let
    # each generation stay self-consistent and cost only one refetch.
    #
    # Two keys rather than one, because the map has two independent
    # lifetimes. "authmap:v2:fresh" is a short-lived sentinel recording that a
    # refresh was *attempted*; "authmap:v2:data" holds the last map that was
    # actually fetched and outlives it by a long way. That split is what
    # lets a DNT outage keep enforcing from a stale map (safe) instead of
    # falling through to fail-open (not safe), while still costing at most
    # one fetch attempt per sentinel TTL.
    #
    # Shared for the same reason identities are: prod runs several
    # serving-api replicas, and a per-process map means one replica can
    # still be enforcing a revoked policy — or paying its own cold-start
    # fail-open — while the others have moved on.

    def set_auth_map(self, auth_map: dict, ttl: int = 3600) -> bool:
        """
        Cache the model → authorization-label map.

        Args:
            auth_map: model_id → list of ``authorization`` label values
            ttl: how long the map stays servable as stale data, in seconds

        Returns:
            True if successful, False otherwise
        """
        try:
            if ttl <= 0:
                # "Servable for zero seconds" is a drop, not a write. Spelled
                # out because redis rejects a non-positive expiry outright,
                # which would otherwise leave the previous map in place.
                return self.clear_auth_map()
            if self.redis_client:
                return bool(
                    self.redis_client.setex(
                        "authmap:v2:data", ttl, json.dumps(auth_map)
                    )
                )
            else:
                self._fallback_auth_map = auth_map
                self._fallback_auth_map_expires_at = time.time() + ttl
                return True
        except Exception as e:
            logger.error(f"Error caching authorization map: {e}")
            return False

    def get_auth_map(self) -> dict | None:
        """
        Look up the cached model → authorization-label map.

        Returns:
            The map, or None when nothing is cached (also on any Redis or
            decode error — the caller treats that as a cold start, which is
            the conservative reading of "we don't know").
        """
        try:
            if self.redis_client:
                raw = self.redis_client.get("authmap:v2:data")
                return json.loads(raw) if raw else None
            else:
                if time.time() >= self._fallback_auth_map_expires_at:
                    self._fallback_auth_map = None
                return self._fallback_auth_map
        except Exception as e:
            logger.error(f"Error reading cached authorization map: {e}")
            return None

    def mark_auth_map_fetched(self, ttl: int) -> bool:
        """
        Record that a refresh was just attempted, successful or not, so that
        every replica backs off for ``ttl`` seconds instead of each paying
        the DNT fetch timeout on every request while it is down.

        Returns:
            True if successful, False otherwise
        """
        try:
            if self.redis_client:
                return bool(self.redis_client.setex("authmap:v2:fresh", ttl, "1"))
            else:
                self._fallback_auth_map_fresh_until = time.time() + ttl
                return True
        except Exception as e:
            logger.error(f"Error marking authorization map fetched: {e}")
            return False

    def auth_map_is_fresh(self) -> bool:
        """
        Has a refresh been attempted recently enough to skip another one?

        Returns:
            True while the sentinel is live, False otherwise (including on
            any Redis error, so a cache failure becomes a refetch rather
            than an indefinitely stale map).
        """
        try:
            if self.redis_client:
                return self.redis_client.exists("authmap:v2:fresh") > 0
            else:
                return time.time() < self._fallback_auth_map_fresh_until
        except Exception as e:
            logger.error(f"Error checking authorization map freshness: {e}")
            return False

    def invalidate_auth_map_freshness(self) -> bool:
        """
        Drop only the freshness sentinel, so the next check refreshes the
        map while the current one stays servable as stale data in the
        meantime. The way to force a re-read of the DNT without opening a
        fail-open window.

        Returns:
            True if successful, False otherwise
        """
        try:
            if self.redis_client:
                self.redis_client.delete("authmap:v2:fresh")
                return True
            else:
                self._fallback_auth_map_fresh_until = 0.0
                return True
        except Exception as e:
            logger.error(f"Error invalidating authorization map freshness: {e}")
            return False

    def clear_auth_map(self) -> bool:
        """
        Drop the cached authorization map and its freshness sentinel,
        leaving token validity and identities untouched.

        Returns:
            True if successful, False otherwise
        """
        try:
            if self.redis_client:
                self.redis_client.delete("authmap:v2:data", "authmap:v2:fresh")
                return True
            else:
                self._fallback_auth_map = None
                self._fallback_auth_map_expires_at = 0.0
                self._fallback_auth_map_fresh_until = 0.0
                return True
        except Exception as e:
            logger.error(f"Error clearing cached authorization map: {e}")
            return False

    # ── post-launch access overrides ─────────────────────────────────────
    #
    # The override table is the authority; this is just a read-through cache
    # so the inference hot path doesn't hit Postgres per request. Unlike the
    # authorization map there is no stale/fresh split, because we control
    # every write: set_override and clear_override drop this key, so a
    # change takes effect on the next request across all replicas. The TTL
    # is only a safety net for an invalidation that never landed.

    def set_access_overrides(self, overrides: dict, ttl: int = 30) -> bool:
        """
        Cache the launch_id → policy map read from model_access_override.

        Returns:
            True if successful, False otherwise
        """
        try:
            if ttl <= 0:
                return self.clear_access_overrides()
            if self.redis_client:
                return bool(
                    self.redis_client.setex(
                        "access:overrides", ttl, json.dumps(overrides)
                    )
                )
            else:
                self._fallback_overrides = overrides
                self._fallback_overrides_expires_at = time.time() + ttl
                return True
        except Exception as e:
            logger.error(f"Error caching access overrides: {e}")
            return False

    def get_access_overrides(self) -> dict | None:
        """
        Look up the cached launch_id → policy map.

        Returns:
            The map, or None when nothing is cached (also on any Redis or
            decode error, so a cache failure becomes a DB read rather than
            silently dropping everyone's overrides).
        """
        try:
            if self.redis_client:
                raw = self.redis_client.get("access:overrides")
                return json.loads(raw) if raw else None
            else:
                if time.time() >= self._fallback_overrides_expires_at:
                    self._fallback_overrides = None
                return self._fallback_overrides
        except Exception as e:
            logger.error(f"Error reading cached access overrides: {e}")
            return None

    def clear_access_overrides(self) -> bool:
        """
        Drop the cached override map — called on every write, so an access
        change takes effect on the next request on every replica rather
        than after the TTL on the ones that didn't serve the write.

        Returns:
            True if successful, False otherwise
        """
        try:
            if self.redis_client:
                self.redis_client.delete("access:overrides")
                return True
            else:
                self._fallback_overrides = None
                self._fallback_overrides_expires_at = 0.0
                return True
        except Exception as e:
            logger.error(f"Error clearing cached access overrides: {e}")
            return False

    # ── grace clock for override pruning ─────────────────────────────────
    #
    # launch_id → the unix time we FIRST failed to find that launch on the
    # mesh. prune_dead_overrides deletes a row only once its launch has been
    # missing continuously for the grace window, because a single DNT read
    # can be partial (a heartbeat gap, a mesh that has not converged) and
    # deleting on one is how a private model silently goes back to public.
    #
    # Shared so the window means the same thing on every replica, and only
    # ever consulted from the admin housekeeping path — never from
    # enforcement. Losing it (a flush, an expiry) restarts the clock, which
    # can only ever DELAY a delete: the safe direction for bookkeeping whose
    # whole job is not deleting too eagerly.
    #
    # Absolute unix timestamps rather than monotonic ones, since they are
    # compared across processes and across restarts.

    def set_missing_launches(self, missing: dict, ttl: int = 604800) -> bool:
        """
        Record launch_id → first-seen-missing time.

        The TTL must comfortably exceed the grace window, or the clock would
        reset before it could ever elapse and nothing would be pruned.

        Returns:
            True if successful, False otherwise
        """
        try:
            if ttl <= 0:
                return self.clear_missing_launches()
            if self.redis_client:
                return bool(
                    self.redis_client.setex("access:missing", ttl, json.dumps(missing))
                )
            else:
                self._fallback_missing = missing
                self._fallback_missing_expires_at = time.time() + ttl
                return True
        except Exception as e:
            logger.error(f"Error caching missing launches: {e}")
            return False

    def get_missing_launches(self) -> dict | None:
        """
        Look up the launch_id → first-seen-missing map.

        Returns:
            The map, or None when nothing is cached (also on any Redis or
            decode error, which restarts the grace clock rather than
            pruning against a map we could not read).
        """
        try:
            if self.redis_client:
                raw = self.redis_client.get("access:missing")
                return json.loads(raw) if raw else None
            else:
                if time.time() >= self._fallback_missing_expires_at:
                    self._fallback_missing = None
                return self._fallback_missing
        except Exception as e:
            logger.error(f"Error reading cached missing launches: {e}")
            return None

    def clear_missing_launches(self) -> bool:
        """
        Forget the grace clock, so every override starts its window afresh.

        Returns:
            True if successful, False otherwise
        """
        try:
            if self.redis_client:
                self.redis_client.delete("access:missing")
                return True
            else:
                self._fallback_missing = None
                self._fallback_missing_expires_at = 0.0
                return True
        except Exception as e:
            logger.error(f"Error clearing cached missing launches: {e}")
            return False

    def clear_cache(self) -> bool:
        """
        Clear all cached tokens, their resolved identities, and the
        authorization map

        Returns:
            True if successful, False otherwise
        """
        try:
            if self.redis_client:
                keys = (
                    self.redis_client.keys("token:*")
                    + self.redis_client.keys("email:*")
                    + self.redis_client.keys("authmap:*")
                    + self.redis_client.keys("access:*")
                )
                if keys:
                    return self.redis_client.delete(*keys) > 0
                return True
            else:
                # Fallback to in-memory
                self._fallback_cache.clear()
                self._fallback_emails.clear()
                self.clear_auth_map()
                self.clear_access_overrides()
                self.clear_missing_launches()
                return True
        except Exception as e:
            logger.error(f"Error clearing cache: {e}")
            return False

    def get_cache_stats(self) -> dict:
        """
        Get cache statistics

        Returns:
            Dictionary with cache statistics
        """
        try:
            if self.redis_client:
                info = self.redis_client.info()
                keys = self.redis_client.keys("token:*")
                return {
                    "connected": True,
                    "token_count": len(keys),
                    "memory_usage": info.get("used_memory_human", "N/A"),
                    "connections": info.get("connected_clients", 0),
                    "redis_version": info.get("redis_version", "N/A"),
                }
            else:
                return {
                    "connected": False,
                    "token_count": len(self._fallback_cache),
                    "fallback_mode": True,
                }
        except Exception as e:
            logger.error(f"Error getting cache stats: {e}")
            return {"error": str(e)}


# Global instance
_token_cache = None


def get_token_cache() -> RedisTokenCache:
    """Get the global token cache instance"""
    global _token_cache
    if _token_cache is None:
        _token_cache = RedisTokenCache()
    return _token_cache
