"""
Kubernetes API client factory.

Loads either the in-cluster service account (when the dashboard itself
runs as a pod in the K3s cluster) or a kubeconfig file (when running from
a workstation/dev box), based on `settings.KUBE_IN_CLUSTER`.

Clients are created once and reused (they're thread-safe for the
synchronous calls we make via `asyncio.to_thread`).
"""

import logging
from functools import lru_cache

from kubernetes import client, config as k8s_config
from kubernetes.client import ApiClient

from app.core.config import settings

logger = logging.getLogger("rca.k8s_client")


@lru_cache(maxsize=1)
def _load_config() -> ApiClient:
    if settings.KUBE_IN_CLUSTER:
        logger.info("Loading in-cluster Kubernetes config")
        k8s_config.load_incluster_config()
    else:
        logger.info("Loading kubeconfig from %s", settings.KUBECONFIG_PATH)
        k8s_config.load_kube_config(config_file=settings.KUBECONFIG_PATH)
    return ApiClient()


@lru_cache(maxsize=1)
def get_core_v1() -> client.CoreV1Api:
    return client.CoreV1Api(_load_config())


@lru_cache(maxsize=1)
def get_apps_v1() -> client.AppsV1Api:
    return client.AppsV1Api(_load_config())


@lru_cache(maxsize=1)
def get_version_api() -> client.VersionApi:
    return client.VersionApi(_load_config())


def reset_clients():
    """Used by tests / reconnect logic to force re-reading kubeconfig."""
    _load_config.cache_clear()
    get_core_v1.cache_clear()
    get_apps_v1.cache_clear()
    get_version_api.cache_clear()
