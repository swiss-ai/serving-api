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

try:
    import firebase_admin
    from firebase_admin import credentials, firestore

    FIREBASE_AVAILABLE = True
except ImportError:
    FIREBASE_AVAILABLE = False

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

        self.db = None

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

        # Firebase disabled — to re-enable, set ENABLE_FIREBASE=true
        if os.environ.get("ENABLE_FIREBASE", "").lower() in ("true", "1"):
            self._init_firebase()
        self.initialized = True

    def _init_firebase(self):
        if not FIREBASE_AVAILABLE:
            logger.warning(
                "firebase-admin not installed. Metrics will not be synced to Firestore."
            )
            return

        cred_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON")
        if not cred_json:
            logger.warning(
                "FIREBASE_SERVICE_ACCOUNT_JSON not set. Metrics will not be synced."
            )
            return

        try:
            if cred_json.strip().startswith("{"):
                service_account_info = json.loads(cred_json)
                cred = credentials.Certificate(service_account_info)
            elif os.path.isfile(cred_json):
                cred = credentials.Certificate(cred_json)
            else:
                service_account_info = json.loads(cred_json)
                cred = credentials.Certificate(service_account_info)
            try:
                firebase_admin.get_app()
            except ValueError:
                firebase_admin.initialize_app(cred)

            self.db = firestore.client()
            logger.info("Firebase initialized successfully.")
        except Exception as e:
            logger.error(f"Failed to initialize Firebase: {e}")

    def record(
        self,
        model: str,
        node_id: str,
        dnt_endpoint: str,
        concurrency: int,
        ttft: float,
        latency: float,
        throughput: float,
    ):
        if not self.db:
            return

        hardware = get_hardware_spec(node_id, dnt_endpoint)

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
                target=self._sync_to_firestore,
                args=(model, hardware, conc_bucket, stats_to_sync),
            ).start()

    def _sync_to_firestore(self, model, hardware, conc_bucket, stats):
        try:
            doc_id = f"{model}_{hardware}_{conc_bucket}".replace("/", "_")
            doc_ref = self.db.collection("llm_benchmarks").document(doc_id)

            @firestore.transactional
            def update_in_transaction(transaction, doc_ref, stats):
                snapshot = doc_ref.get(transaction=transaction)
                if snapshot.exists:
                    existing = snapshot.to_dict()
                    new_count = existing.get("count", 0) + stats["count"]
                    new_avg_ttft = (
                        existing.get("avg_ttft", 0) * existing.get("count", 0)
                        + stats["total_ttft"]
                    ) / new_count
                    new_avg_latency = (
                        existing.get("avg_latency", 0) * existing.get("count", 0)
                        + stats["total_latency"]
                    ) / new_count
                    new_avg_throughput = (
                        existing.get("avg_throughput", 0) * existing.get("count", 0)
                        + stats["total_throughput"]
                    ) / new_count

                    transaction.update(
                        doc_ref,
                        {
                            "count": new_count,
                            "avg_ttft": new_avg_ttft,
                            "avg_latency": new_avg_latency,
                            "avg_throughput": new_avg_throughput,
                            "last_updated": firestore.SERVER_TIMESTAMP,
                        },
                    )
                else:
                    new_count = stats["count"]
                    transaction.set(
                        doc_ref,
                        {
                            "model": model,
                            "hardware": hardware,
                            "concurrency": conc_bucket,
                            "count": new_count,
                            "avg_ttft": stats["total_ttft"] / new_count,
                            "avg_latency": stats["total_latency"] / new_count,
                            "avg_throughput": stats["total_throughput"] / new_count,
                            "last_updated": firestore.SERVER_TIMESTAMP,
                        },
                    )

            transaction = self.db.transaction()
            update_in_transaction(transaction, doc_ref, stats)
            logger.info(f"Synced stats for {doc_id}")

        except Exception as e:
            logger.error(f"Error syncing to Firestore: {e}")

    def get_benchmark_data(self, model: Optional[str] = None) -> list[Dict[str, Any]]:
        if not self.db:
            return []

        try:
            ref = self.db.collection("llm_benchmarks")
            if model:
                query = ref.where("model", "==", model)
                docs = query.stream()
            else:
                docs = ref.stream()

            results = []
            for doc in docs:
                data = doc.to_dict()
                results.append(data)
            return results
        except Exception as e:
            logger.error(f"Error fetching benchmark data: {e}")
            return []


# Global singleton
metrics_collector = MetricsCollector()
