"""
Thin async wrapper around the Longhorn Manager REST API.

Longhorn's manager exposes a HATEOAS-ish JSON API (typically reachable in-
cluster at http://longhorn-frontend.longhorn-system.svc.cluster.local:80/v1,
or via `kubectl -n longhorn-system port-forward svc/longhorn-frontend 9500:80`
from outside the cluster). We only ever read from it here - the dashboard
never mutates volumes/replicas/engines.

Two collections matter for RCA:
  - /v1/volumes  -> volume state/robustness, its engine, and (nested or via
                    a follow-up call) its replicas
  - /v1/volumes/{name}/replicas is not always present as a sub-resource in
    every Longhorn version, so we instead read `replicas` off the volume's
    own "controllers"/"replicas" fields where available and fall back to
    the standalone /v1/replicas collection, filtering by volume name.
"""

import logging
import aiohttp

from app.core.config import settings

logger = logging.getLogger("rca.longhorn_client")


async def _get_collection(session: aiohttp.ClientSession, path: str) -> list[dict]:
    """GET a Longhorn collection endpoint and return its `data` list.
    Returns [] on any error (logged) so a Longhorn-manager hiccup never
    takes down the whole collection cycle."""
    url = f"{settings.LONGHORN_API_URL.rstrip('/')}/{path.lstrip('/')}"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                logger.warning("Longhorn API GET %s failed (%s)", path, resp.status)
                return []
            payload = await resp.json()
            return payload.get("data", []) or []
    except (aiohttp.ClientError, TimeoutError) as e:
        logger.warning("Longhorn manager unreachable for '%s': %s", path, e)
        return []
    except ValueError as e:
        logger.warning("Longhorn API returned non-JSON for '%s': %s", path, e)
        return []


async def get_volumes(session: aiohttp.ClientSession) -> list[dict]:
    return await _get_collection(session, "volumes")


async def get_replicas(session: aiohttp.ClientSession) -> list[dict]:
    return await _get_collection(session, "replicas")


async def get_engines(session: aiohttp.ClientSession) -> list[dict]:
    return await _get_collection(session, "engines")


async def get_events(session: aiohttp.ClientSession) -> list[dict]:
    """Longhorn surfaces its own Kubernetes Events (VolumeRebuild,
    AttachedVolume, etc.) through the standard /v1/events collection on
    newer manager versions. Older versions don't expose this endpoint, in
    which case we just return [] and rely on volume/replica/engine state
    transitions instead - the K8s collector's own event watch also
    already picks these up from the Kubernetes Events API since Longhorn
    posts events against the PVC/PV objects."""
    return await _get_collection(session, "events")
