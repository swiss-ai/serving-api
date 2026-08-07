import logging
import time
import requests
from collections import defaultdict
from typing import Dict, Any, Optional
from threading import Lock
from functools import lru_cache
from backend.config import parse_hardware_info, get_settings

logger = logging.getLogger(__name__)


def get_ttl_hash(seconds=24 * 3600):
    """Return the same value within `seconds` time period"""
    return round(time.time() / seconds)


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
# get slower after a deploy?", load/concurrency-performance curves,
# regression alerting) a TSDB is the right tool: the cluster already runs Prometheus + Grafana, and the vLLM/SGLang
# engines natively expose per-model metrics — build such views there (or
# add a /metrics endpoint here with model/hardware-labelled histograms)
# rather than growing this table into a homemade time series.


def sync_benchmark(engine, model: str, hardware: str, stats: dict):
    """Upsert this month's (model, hardware) row with merged averages.
    Monthly buckets keep the page fresh: old months age out of the read
    window instead of biasing an all-time average forever."""
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
            .with_for_update()
        ).first()
        if row is None:
            row = PerfBenchmark(month=month, model=model, hardware=hardware)
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
    """Merge the last `months` monthly buckets per (model, hardware) into
    one weighted-average row each."""
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
        key = (r.model, r.hardware)
        agg = merged.get(key)
        if agg is None:
            agg = {
                "model": r.model,
                "hardware": r.hardware,
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

        key = (model, hardware)

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
                args=(model, hardware, stats_to_sync),
            ).start()

    def _sync_to_db(self, model, hardware, stats):
        try:
            sync_benchmark(self._get_engine(), model, hardware, stats)
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
