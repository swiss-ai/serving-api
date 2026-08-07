from typing import Optional
from functools import lru_cache
from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from backend.services.metrics_service import (
    get_ttl_hash,
    metrics_collector,
)

router = APIRouter()


@router.get("/v1/leaderboard")
async def get_leaderboard(request: Request, days: int = 30):
    """Public model ranking by token usage over the window.

    Reads the Postgres usage_daily counters — the same store behind My
    Usage and the admin Users page, so every page agrees on the numbers.
    This replaced the Langfuse daily-metrics read path; Langfuse now only
    stores monitored recordings.
    """
    from backend.services.usage_service import usage_by_model

    if days < 1 or days > 365:
        raise HTTPException(status_code=422, detail="days must be 1..365")
    models = await run_in_threadpool(usage_by_model, request.app.state.engine, days)
    return {"days": days, "models": models}


@lru_cache(maxsize=32)
def get_perf_data(model: Optional[str] = None, ttl_hash: int = None):
    return metrics_collector.get_benchmark_data(model)


@router.get("/v1/perf")
async def get_perf_endpoint(request: Request):
    ttl = get_ttl_hash()
    model = request.query_params.get("model")
    data = await run_in_threadpool(get_perf_data, model=model, ttl_hash=ttl)
    return dict(object="list", data=data)
