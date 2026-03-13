import os
import json
import logging
from collections import defaultdict
from typing import Dict, Any, Optional
from threading import Lock

try:
    import firebase_admin
    from firebase_admin import credentials, firestore

    FIREBASE_AVAILABLE = True
except ImportError:
    FIREBASE_AVAILABLE = False

from backend.utils import get_hardware_spec

logger = logging.getLogger(__name__)


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
            # support both path and json content
            if cred_json.strip().startswith("{"):
                service_account_info = json.loads(cred_json)
                cred = credentials.Certificate(service_account_info)
            elif os.path.isfile(cred_json):
                cred = credentials.Certificate(cred_json)
            else:
                # If it's not a file and doesn't look like JSON, try parsing as JSON anyway
                # (maybe encoded or malformed, let loads handle or fail)
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
        """
        Record metrics for a request. Aggregates locally and syncs to Firestore.
        """
        if not self.db:
            # If no DB, we might still want to log or just return
            # For now, we just skip to avoid memory growth if we never sync
            return

        # 1. Get Hardware Info
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

        # 3. Aggregate Locally
        should_sync = False
        with self.buffer_lock:
            entry = self.local_buffer[key]
            entry["count"] += 1
            entry["total_ttft"] += ttft
            entry["total_latency"] += latency
            entry["total_throughput"] += throughput
            # Sum of squares could be used for variance/stddev if needed later
            # entry["sum_sq_ttft"] += ttft ** 2

            if entry["count"] >= self.sync_threshold:
                # Copy and reset buffer for this key
                stats_to_sync = entry.copy()
                entry["count"] = 0
                entry["total_ttft"] = 0.0
                entry["total_latency"] = 0.0
                entry["total_throughput"] = 0.0
                should_sync = True

        # 4. Sync to Firestore (Async preferred, but using thread/background task logic)
        # For simplicity in this step, we just run it. Ideally this should be fire-and-forget or async.
        if should_sync:
            # We can use asyncio.create_task if we are in an async context,
            # likely yes since this is called from proxy (main loop).
            # However this class doesn't know about the loop easily unless passed.
            # We'll assume the caller wraps this or we just do it synchronously for now
            # (Firestore writes are fast enough? Maybe not).
            # Let's try to run it in a separate thread or use firestore's async capabilities if available.
            # The standard firebase-admin is synchronous blocking.
            # We will use a simple ThreadPool or similar if needed,
            # but for the plan, let's keep it simple: run in a thread.
            import threading

            threading.Thread(
                target=self._sync_to_firestore,
                args=(model, hardware, conc_bucket, stats_to_sync),
            ).start()

    def _sync_to_firestore(self, model, hardware, conc_bucket, stats):
        try:
            # Key construction
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
                            # Store snapshot of recent raw sum if needed for precision, but averages are fine for now
                        },
                    )
                else:
                    new_count = stats["count"]
                    transaction.set(
                        doc_ref,
                        {
                            "model": model,
                            "hardware": hardware,
                            "concurrency": conc_bucket,  # readable string
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
                # Note: This might require a composite index if combined with other filters,
                # but for single field it should be fine or prompts for index creation.
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
