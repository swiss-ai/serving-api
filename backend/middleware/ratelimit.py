from fastapi import Depends, HTTPException

from backend.middleware.auth import require_auth
from backend.services.rate_limit_service import check_rate_limit


async def rate_limited(token: str = Depends(require_auth)) -> str:
    """Drop-in replacement for require_auth on inference routes: same
    return value, but the request also counts against the caller's
    per-minute rate limit. The 429 is raised as a plain HTTPException so
    backend.main's handler wraps it in the OpenAI error envelope
    (type=rate_limit_error)."""
    decision = check_rate_limit(token)
    if not decision.allowed:
        raise HTTPException(
            status_code=429,
            detail=(
                f"Rate limit of {decision.limit} requests per minute exceeded. "
                f"Retry after {decision.retry_after} seconds."
            ),
            headers={
                "Retry-After": str(decision.retry_after),
                "X-RateLimit-Limit": str(decision.limit),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": str(decision.retry_after),
            },
        )
    return token
