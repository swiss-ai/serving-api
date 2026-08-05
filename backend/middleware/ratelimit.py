from fastapi import HTTPException

from backend.services.rate_limit_service import check_rate_limit


def enforce_rate_limit(token: str) -> None:
    """Count this request against the caller's per-minute budget and raise
    when exceeded. Called after model routing resolves to an external
    passthrough provider — OpenTela-served models are backed by the user's
    own GPU allocation and are deliberately not limited. The 429 is a
    plain HTTPException so backend.main's handler wraps it in the OpenAI
    error envelope (type=rate_limit_error)."""
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
