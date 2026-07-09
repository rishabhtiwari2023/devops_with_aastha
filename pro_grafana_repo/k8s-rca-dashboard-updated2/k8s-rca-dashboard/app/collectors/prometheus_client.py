"""
Thin async wrapper around Prometheus's HTTP API (instant queries only —
this dashboard always wants "current value", with history coming from
our own SQLite tables rather than re-querying Prometheus's own history).
"""

import logging
import aiohttp

from app.core.config import settings

logger = logging.getLogger("rca.prometheus_client")


async def instant_query(session: aiohttp.ClientSession, promql: str) -> list[dict]:
    """Run one PromQL instant query, return the raw `result` vector (list of
    {"metric": {...labels...}, "value": [timestamp, "value_str"]} dicts).
    Returns [] on any error (logged) so a single bad/slow query never takes
    down the whole collection cycle."""
    if not settings.PROMETHEUS_URL:
        logger.debug("Skipping Prometheus query because PROMETHEUS_URL is not configured")
        return []

    url = f"{settings.PROMETHEUS_URL.rstrip('/')}/api/v1/query"
    try:
        async with session.get(url, params={"query": promql}, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                logger.warning("Prometheus query failed (%s): %s", resp.status, promql)
                return []
            payload = await resp.json()
            if payload.get("status") != "success":
                logger.warning("Prometheus query error: %s -> %s", promql, payload.get("error"))
                return []
            return payload.get("data", {}).get("result", [])
    except (aiohttp.ClientError, TimeoutError) as e:
        logger.warning("Prometheus unreachable for query '%s': %s", promql, e)
        return []
