import os
import json
import logging
import base64
import time
import requests
import aiohttp
from collections import defaultdict
from typing import Dict, Any, Optional
from threading import Lock
from functools import lru_cache
from backend.config import parse_hardware_info, get_settings

logger = logging.getLogger(__name__)


def _daily_metrics_endpoint() -> str:
    """Daily-metrics API on the configured Langfuse (self-hosted since
    2026-08-05); cloud remains the fallback when no host is configured."""
    host = get_settings().langfuse_host.rstrip("/") or "https://cloud.langfuse.com"
    return f"{host}/api/public/metrics/daily"


def get_ttl_hash(seconds=24 * 3600):
    """Return the same value within `seconds` time period"""
    return round(time.time() / seconds)


@lru_cache()
def get_statistics(api_key: Optional[str] = None, ttl_hash=None):
    # Langfuse disabled — to re-enable, set LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY
    username = os.getenv("LANGFUSE_PUBLIC_KEY")
    password = os.getenv("LANGFUSE_SECRET_KEY")
    if not username or not password:
        return {}
    lf_endpoint = _daily_metrics_endpoint()
    if api_key is not None:
        lf_endpoint += f"?userId={api_key}"
    data = {}
    try:
        response = requests.get(lf_endpoint, auth=(username, password))
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.HTTPError as errh:
        print(f"HTTP Error: {errh}")
    except requests.exceptions.ConnectionError as errc:
        print(f"Error Connecting: {errc}")
    except requests.exceptions.Timeout as errt:
        print(f"Timeout Error: {errt}")
    except requests.exceptions.RequestException as err:
        print(f"Error: {err}")
    return data


@lru_cache(maxsize=128)
def get_hardware_spec(node_id: str, dnt_endpoint: str) -> str:
    """Fetch and parse hardware spec for a node, with caching."""
    try:
        resp = requests.get(dnt_endpoint, timeout=2)
        if resp.status_code == 200:
            data = resp.json()
            node_info = data.get(f"/{node_id}")
            if node_info:
                return parse_hardware_info(node_info.get("hardware"))
    except Exception as e:
        logger.warning(f"Failed to fetch hardware info for node {node_id}: {e}")
    return "Unknown"


_metrics_cache = {}


def summarize_daily_usage(days: list[dict]) -> dict:
    """Daily-metrics rows -> the leaderboard shape the frontend expects
    ({data: [{providedModelName, sum_totalTokens}]}, tokens descending)."""
    per_model: dict[str, int] = defaultdict(int)
    for day in days:
        for u in day.get("usage") or []:
            model = u.get("model")
            if not model:
                continue
            total = u.get("totalUsage")
            if total is None:
                total = (u.get("inputUsage") or 0) + (u.get("outputUsage") or 0)
            per_model[model] += int(total or 0)
    data = [
        {"providedModelName": m, "sum_totalTokens": str(n)}
        for m, n in sorted(per_model.items(), key=lambda kv: -kv[1])
    ]
    return {"data": data}


async def get_langfuse_metrics(query_json: dict, ttl_hash: int = None):
    """Serve the leaderboard query from Langfuse's daily-metrics API.

    The frontend sends a v2-metrics-style query (sum totalTokens by model
    over a window), but that API is v4-only and the self-hosted instance
    runs v3. The daily API exists on both and carries usage-by-model, so we
    aggregate server-side and answer in the shape the frontend expects."""
    settings = get_settings()
    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        return {}
    if not settings.langfuse_host:
        return {}

    query_str = json.dumps(query_json, sort_keys=True)
    cache_key = (query_str, ttl_hash)

    if cache_key in _metrics_cache:
        return _metrics_cache[cache_key]

    params = {"limit": "100"}
    if query_json.get("fromTimestamp"):
        params["fromTimestamp"] = query_json["fromTimestamp"]
    if query_json.get("toTimestamp"):
        params["toTimestamp"] = query_json["toTimestamp"]

    auth_s = f"{settings.langfuse_public_key}:{settings.langfuse_secret_key}"
    auth_b64 = base64.b64encode(auth_s.encode()).decode()
    headers = {"Authorization": f"Basic {auth_b64}"}
    base = f"{settings.langfuse_host.rstrip('/')}/api/public/metrics/daily"

    days: list[dict] = []
    try:
        async with aiohttp.ClientSession() as session:
            for page in range(1, 11):  # up to 1000 days — effectively all
                async with session.get(
                    base,
                    params={**params, "page": str(page)},
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status != 200:
                        logger.warning(
                            "Langfuse daily metrics unavailable (%s): %s",
                            resp.status,
                            (await resp.text())[:200],
                        )
                        return {}
                    payload = await resp.json()
                days.extend(payload.get("data") or [])
                meta = payload.get("meta") or {}
                if page >= (meta.get("totalPages") or 1):
                    break
    except Exception as exc:
        # Never let a Langfuse outage 500 the leaderboard; don't cache so a
        # recovered Langfuse is picked up on the next request.
        logger.warning("Langfuse daily metrics request failed: %s", exc)
        return {}

    data = summarize_daily_usage(days)
    _metrics_cache[cache_key] = data
    return data


def merged_averages(prev_count: int, prev_avgs: dict, stats: dict) -> tuple[int, dict]:
    """Fold a buffered batch (count + totals) into running averages."""
    new_count = prev_count + stats["count"]
    merged = {}
    for key, total_key in (
        ("avg_ttft", "total_ttft"),
        ("avg_latency", "total_latency"),
        ("avg_throughput", "total_throughput"),
    ):
        merged[key] = (
            prev_avgs.get(key, 0.0) * prev_count + stats[total_key]
        ) / new_count
    return new_count, merged


# NOTE on the storage choice: postgres suits this workload — a small keyed
# set of aggregates behind a public page (bounded rows, upserts, no time
# axis rendered). For time-resolved questions (p95 latency, "did this model
# get slower after a deploy?", regression alerting) a TSDB is the right
# tool: the cluster already runs Prometheus + Grafana, and the vLLM/SGLang
# engines natively expose per-model metrics — build such views there (or
# add a /metrics endpoint here with model/hardware-labelled histograms)
# rather than growing this table into a homemade time series.


def sync_benchmark(engine, model: str, hardware: str, conc_bucket: str, stats: dict):
    """Upsert this month's (model, hardware, concurrency) row with merged
    averages. Monthly buckets keep the page fresh: old months age out of
    the read window instead of biasing an all-time average forever."""
    from datetime import datetime

    from sqlmodel import Session, select

    from backend.models.entities import PerfBenchmark

    month = datetime.now().strftime("%Y-%m")
    with Session(engine) as session:
        row = session.exec(
            select(PerfBenchmark)
            .where(PerfBenchmark.month == month)
            .where(PerfBenchmark.model == model)
            .where(PerfBenchmark.hardware == hardware)
            .where(PerfBenchmark.concurrency == conc_bucket)
            .with_for_update()
        ).first()
        if row is None:
            row = PerfBenchmark(
                month=month, model=model, hardware=hardware, concurrency=conc_bucket
            )
        new_count, merged = merged_averages(
            row.count,
            {
                "avg_ttft": row.avg_ttft,
                "avg_latency": row.avg_latency,
                "avg_throughput": row.avg_throughput,
            },
            stats,
        )
        row.count = new_count
        row.avg_ttft = merged["avg_ttft"]
        row.avg_latency = merged["avg_latency"]
        row.avg_throughput = merged["avg_throughput"]
        row.last_updated = datetime.now()
        session.add(row)
        session.commit()


def fetch_benchmarks(
    engine, model: Optional[str] = None, months: int = 3
) -> list[Dict[str, Any]]:
    """Merge the last `months` monthly buckets per (model, hardware,
    concurrency) into one weighted-average row each."""
    from datetime import datetime

    from sqlmodel import Session, select

    from backend.models.entities import PerfBenchmark

    now = datetime.now()
    # "YYYY-MM" sorts lexicographically, so a string cutoff works.
    y, m = now.year, now.month - (months - 1)
    while m < 1:
        m += 12
        y -= 1
    cutoff = f"{y:04d}-{m:02d}"

    with Session(engine) as session:
        query = select(PerfBenchmark).where(PerfBenchmark.month >= cutoff)
        if model:
            query = query.where(PerfBenchmark.model == model)
        rows = session.exec(query).all()

    merged: Dict[tuple, Dict[str, Any]] = {}
    for r in rows:
        key = (r.model, r.hardware, r.concurrency)
        agg = merged.get(key)
        if agg is None:
            agg = {
                "model": r.model,
                "hardware": r.hardware,
                "concurrency": r.concurrency,
                "count": 0,
                "avg_ttft": 0.0,
                "avg_latency": 0.0,
                "avg_throughput": 0.0,
                "last_updated": r.last_updated,
            }
            merged[key] = agg
        total = agg["count"] + r.count
        if total:
            for f in ("avg_ttft", "avg_latency", "avg_throughput"):
                agg[f] = (agg[f] * agg["count"] + getattr(r, f) * r.count) / total
        agg["count"] = total
        agg["last_updated"] = max(agg["last_updated"], r.last_updated)
    return list(merged.values())


class MetricsCollector:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(MetricsCollector, cls).__new__(cls)
            cls._instance.initialized = False
        return cls._instance

    def __init__(self):
        if self.initialized:
            return

        self._engine = None

        self.local_buffer = defaultdict(
            lambda: {
                "count": 0,
                "total_ttft": 0.0,
                "total_latency": 0.0,
                "total_throughput": 0.0,
                "sum_sq_ttft": 0.0,
                "sum_sq_latency": 0.0,
            }
        )
        self.buffer_lock = Lock()
        self.sync_threshold = 5
        self.initialized = True

    def _get_engine(self):
        """Lazy engine: the collector is constructed at import time, before
        settings/DB are necessarily ready."""
        if self._engine is None:
            from sqlmodel import create_engine

            settings = get_settings()
            if not settings.database_url:
                return None
            self._engine = create_engine(settings.database_url, pool_pre_ping=True)
        return self._engine

    def record(
        self,
        model: str,
        node_id: str,
        dnt_endpoint: str,
        concurrency: int,
        ttft: float,
        latency: float,
        throughput: float,
        hardware: Optional[str] = None,
    ):
        """`hardware` override: passthrough providers (CSCS L1, RCP) expose
        no node info, so their display label is recorded as the "served on"
        dimension instead of a doomed hardware lookup."""
        if self._get_engine() is None:
            return

        hardware = hardware or get_hardware_spec(node_id, dnt_endpoint)

        if concurrency <= 1:
            conc_bucket = "1"
        elif concurrency <= 10:
            conc_bucket = "2-10"
        elif concurrency <= 50:
            conc_bucket = "11-50"
        elif concurrency <= 100:
            conc_bucket = "51-100"
        else:
            conc_bucket = "101+"

        key = (model, hardware, conc_bucket)

        should_sync = False
        with self.buffer_lock:
            entry = self.local_buffer[key]
            entry["count"] += 1
            entry["total_ttft"] += ttft
            entry["total_latency"] += latency
            entry["total_throughput"] += throughput

            if entry["count"] >= self.sync_threshold:
                stats_to_sync = entry.copy()
                entry["count"] = 0
                entry["total_ttft"] = 0.0
                entry["total_latency"] = 0.0
                entry["total_throughput"] = 0.0
                should_sync = True

        if should_sync:
            import threading

            threading.Thread(
                target=self._sync_to_db,
                args=(model, hardware, conc_bucket, stats_to_sync),
            ).start()

    def _sync_to_db(self, model, hardware, conc_bucket, stats):
        try:
            sync_benchmark(self._get_engine(), model, hardware, conc_bucket, stats)
        except Exception as e:
            logger.error(f"Error syncing benchmark to postgres: {e}")

    def get_benchmark_data(self, model: Optional[str] = None) -> list[Dict[str, Any]]:
        engine = self._get_engine()
        if engine is None:
            return []
        try:
            return fetch_benchmarks(engine, model)
        except Exception as e:
            logger.error(f"Error fetching benchmark data: {e}")
            return []


# Global singleton
metrics_collector = MetricsCollector()
