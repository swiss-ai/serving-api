from typing import Optional
from functools import lru_cache
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.concurrency import run_in_threadpool
from backend.middleware.auth import optional_security
from backend.services.metrics_service import (
    get_statistics,
    get_ttl_hash,
    get_langfuse_metrics,
    metrics_collector,
)

router = APIRouter()


@router.get("/v1/statistics")
async def get_statistics_endpoint(
    credentials: HTTPAuthorizationCredentials | None = Depends(optional_security),
):
    if credentials:
        api_key = credentials.credentials
    else:
        api_key = None
    stats = get_statistics(api_key, ttl_hash=get_ttl_hash())
    return stats


@router.post("/v1/metrics")
async def get_metrics_endpoint(
    request: Request,
):
    res = await request.json()
    ttl = get_ttl_hash(seconds=3600)
    try:
        data = await get_langfuse_metrics(res, ttl_hash=ttl)
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@lru_cache(maxsize=32)
def get_perf_data(model: Optional[str] = None, ttl_hash: int = None):
    return metrics_collector.get_benchmark_data(model)


@router.get("/v1/perf")
async def get_perf_endpoint(request: Request):
    ttl = get_ttl_hash()
    model = request.query_params.get("model")
    data = await run_in_threadpool(get_perf_data, model=model, ttl_hash=ttl)
    return dict(object="list", data=data)
