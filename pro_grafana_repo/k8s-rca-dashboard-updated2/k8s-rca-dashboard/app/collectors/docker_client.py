"""
Docker client factory.

Each K3s node runs its own Docker daemon, so we need one client per node.
`settings.DOCKER_HOSTS` maps node name -> Docker daemon URL (a local unix
socket for the node the dashboard itself runs on, TCP endpoints for the
others - see .env.example for how to point these at your 3-node lab).
"""

import logging
from functools import lru_cache

import docker
from docker.errors import DockerException

from app.core.config import settings

logger = logging.getLogger("rca.docker_client")


@lru_cache(maxsize=None)
def get_docker_client(node_name: str) -> docker.DockerClient:
    host = settings.DOCKER_HOSTS.get(node_name)
    if not host:
        raise DockerException(f"No Docker host configured for node '{node_name}'")
    return docker.DockerClient(base_url=host, timeout=10)


def reset_clients():
    get_docker_client.cache_clear()
