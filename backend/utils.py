import os
import requests
import logging
import json
import base64
import urllib.parse
import aiohttp
from typing import Optional
from functools import lru_cache
import time
from backend.config import parse_hardware_info, get_settings

logger = logging.getLogger(__name__)

base_endpoint = "https://cloud.langfuse.com/api/public/metrics/daily"


@lru_cache()
def get_statistics(api_key: Optional[str] = None, ttl_hash=None):
    # Parse request body for api_key
    lf_endpoint = base_endpoint
    if api_key is not None:
        lf_endpoint += f"?userId={api_key}"
    # Basic authentication credentials
    username = os.getenv("LANGFUSE_PUBLIC_KEY")
    password = os.getenv("LANGFUSE_SECRET_KEY")
    data = {}
    try:
        # Make API request with basic authentication
        response = requests.get(lf_endpoint, auth=(username, password))
        # Check if request was successful
        response.raise_for_status()
        # Parse and print the JSON response
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


def get_ttl_hash(seconds=24 * 3600):
    """Return the same value withing `seconds` time period"""
    return round(time.time() / seconds)


@lru_cache(maxsize=128)
def get_hardware_spec(node_id: str, dnt_endpoint: str) -> str:
    """Fetch and parse hardware spec for a node, with caching."""
    try:
        # We assume dnt_endpoint is like ".../v1/dnt/table"
        # And we need to fetch the whole table to find the node
        # Optimization: In a real distributed system we might want a specific endpoint
        # For now, we fetch the table as per user instruction logic
        resp = requests.get(dnt_endpoint, timeout=2)
        if resp.status_code == 200:
            data = resp.json()
            # print(f"data: {data}")
            node_info = data.get(f"/{node_id}")
            if node_info:
                return parse_hardware_info(node_info.get("hardware"))
    except Exception as e:
        logger.warning(f"Failed to fetch hardware info for node {node_id}: {e}")
    return "Unknown"


# Async cache for metrics
_metrics_cache = {}


async def get_langfuse_metrics(query_json: dict, ttl_hash: int = None):
    """
    Fetch metrics from Langfuse with async caching.
    ttl_hash is used to invalidate cache (controlled by caller).
    """
    query_str = json.dumps(query_json, sort_keys=True)
    cache_key = (query_str, ttl_hash)

    if cache_key in _metrics_cache:
        return _metrics_cache[cache_key]

    settings = get_settings()
    encoded_query = urllib.parse.quote(query_str)
    url = f"{settings.langfuse_host}/api/public/v2/metrics?query={encoded_query}"

    auth_s = f"{settings.langfuse_public_key}:{settings.langfuse_secret_key}"
    auth_b64 = base64.b64encode(auth_s.encode()).decode()
    headers = {"Authorization": f"Basic {auth_b64}"}

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            data = await resp.json()
    _metrics_cache[cache_key] = data
    return data
