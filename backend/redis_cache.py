import os
import redis
import logging

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

    def clear_cache(self) -> bool:
        """
        Clear all cached tokens and their resolved identities

        Returns:
            True if successful, False otherwise
        """
        try:
            if self.redis_client:
                keys = self.redis_client.keys("token:*") + self.redis_client.keys(
                    "email:*"
                )
                if keys:
                    return self.redis_client.delete(*keys) > 0
                return True
            else:
                # Fallback to in-memory
                self._fallback_cache.clear()
                self._fallback_emails.clear()
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
