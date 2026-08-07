"""Admin CRUD over per-user monitoring rules.

Admins are apikey rows with is_admin = true (bootstrap the first one via
SQL). The bearer token may be either the admin's serving API key
(curl-friendly) or their IdP access token (frontend-friendly) — both resolve
to an apikey row whose flag decides. When the IdP exposes a group claim
(Authentik), an admin-group check can be OR-ed in here later; the DB flag
stays the durable base.
"""

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

from backend.services.auth_service import get_profile_from_accesstoken
from backend.services.monitoring_service import (
    SOURCES,
    TTL_CHOICES,
    LEVEL_RANK,
    delete_rule,
    is_admin,
    list_rules,
    resolve_owner_email,
    upsert_rule,
)

router = APIRouter()
security = HTTPBearer()


async def require_admin(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
) -> str:
    """Resolve the caller (API key or IdP token) and require is_admin."""
    engine = request.app.state.engine
    token = credentials.credentials

    email = resolve_owner_email(engine, token)
    if email is not None:
        # Bearer is a serving API key.
        if is_admin(engine, api_key=token):
            return email
        raise HTTPException(status_code=403, detail="Admin access required")

    # Bearer may be an IdP access token.
    try:
        email = get_profile_from_accesstoken(token).get("email")
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid access token")
    if email and is_admin(engine, email=email):
        return email
    raise HTTPException(status_code=403, detail="Admin access required")


class MonitoringRuleIn(BaseModel):
    owner_email: str
    level: str  # 'metadata' | 'full'
    ttl: str  # '1h' | '6h' | '1d' | '7d' | '90d'
    note: str = ""


@router.get("/v1/admin/metrics/users")
async def user_activity(
    request: Request,
    admin: str = Depends(require_admin),
    days: int = 30,
):
    """Most-active users by requests and tokens. Admin only: pairing an
    email with behavioural data is personal information.

    Reads usage_daily rather than Langfuse. The previous implementation
    paginated the trace list and took ~85s against prod's volume while
    returning a 2.6% sample; this is an indexed GROUP BY.
    """
    from backend.services.usage_service import usage_by_user

    if days < 1 or days > 365:
        raise HTTPException(status_code=422, detail="days must be 1..365")
    return {"days": days, "users": usage_by_user(request.app.state.engine, days)}


@router.get("/v1/admin/monitoring/users")
async def list_monitoring_rules(
    request: Request,
    admin: str = Depends(require_admin),
    include_expired: bool = False,
):
    return {
        "rules": list_rules(request.app.state.engine, include_expired=include_expired),
        "levels": sorted(LEVEL_RANK),
        "ttls": sorted(TTL_CHOICES),
    }


@router.post("/v1/admin/monitoring/users")
async def create_monitoring_rule(
    request: Request,
    rule: MonitoringRuleIn,
    admin: str = Depends(require_admin),
):
    try:
        created = upsert_rule(
            request.app.state.engine,
            owner_email=rule.owner_email,
            level=rule.level,
            source="admin",
            ttl=rule.ttl,
            created_by=admin,
            note=rule.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return created.model_dump()


@router.delete("/v1/admin/monitoring/users/{owner_email}")
async def delete_monitoring_rule(
    request: Request,
    owner_email: str,
    admin: str = Depends(require_admin),
    source: Optional[str] = None,
):
    """Remove monitoring for a user. By default only the admin rule; pass
    ?source=self to remove their opt-in, or ?source=all for both."""
    if source is None:
        source = "admin"
    if source not in (*SOURCES, "all"):
        raise HTTPException(status_code=422, detail="source must be admin|self|all")
    removed = delete_rule(
        request.app.state.engine,
        owner_email,
        source=None if source == "all" else source,
    )
    if removed == 0:
        raise HTTPException(status_code=404, detail="No matching rule")
    return {"removed": removed}
