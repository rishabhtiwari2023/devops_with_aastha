"""
Background services orchestrator.

`BackgroundServices.start()` is called once from the FastAPI lifespan
(main.py) and launches all async collector loops, the RCA engine,
the APScheduler (for the retention job), and wires the alert callback
that pushes new RCA verdicts to all WebSocket clients.

Design principles
-----------------
* Each service initialises lazily inside `_safe_start()`.  If a
  collector cannot connect (missing kubeconfig, Docker socket not
  reachable, etc.) it logs a warning and that loop simply doesn't
  start — the rest of the dashboard keeps working.
* `stop()` cancels every asyncio.Task cleanly on shutdown so uvicorn
  can exit without leaving zombie coroutines.
"""

import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core.config import settings
from app.utils.retention import run_retention

logger = logging.getLogger("rca.background")


class BackgroundServices:
    def __init__(self):
        self._tasks: list[asyncio.Task] = []
        self._scheduler = AsyncIOScheduler(timezone="UTC")
        self._rca_engine = None

    # ------------------------------------------------------------------
    # Public lifecycle methods
    # ------------------------------------------------------------------

    async def start(self):
        await self._init_collectors()
        self._start_scheduler()
        logger.info("All background services started")

    async def stop(self):
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
        logger.info("Background services stopped")

    # ------------------------------------------------------------------
    # Collector + engine initialisation
    # ------------------------------------------------------------------

    async def _init_collectors(self):
        from app.rca.engine import RCAEngine
        from app.utils.websocket_manager import manager as ws_manager
        from app.utils.serializers import row_to_dict

        self._rca_engine = RCAEngine()

        def _on_alert(rca_record, alert):
            """Called synchronously from within the running event loop."""
            asyncio.create_task(
                ws_manager.broadcast(
                    {
                        "type": "alert",
                        "rca": row_to_dict(rca_record),
                        "alert": row_to_dict(alert),
                    }
                )
            )

        self._rca_engine.on_alert = _on_alert

        await self._safe_start("kubernetes_collector", self._start_k8s)

        if not settings.DOCKER_HOSTS:
            import os
            try:
                import docker as docker_lib
                if os.name == "nt":
                    # On Windows, check local Docker named pipe
                    client = docker_lib.DockerClient(base_url="npipe:////./pipe/docker_engine", timeout=2)
                    client.ping()
                    settings.DOCKER_HOSTS = {"localhost": "npipe:////./pipe/docker_engine"}
                    logger.info("Auto-configured Docker collector to use local Docker named pipe (npipe:////./pipe/docker_engine)")
                else:
                    # On Unix/Linux, check local Docker socket
                    client = docker_lib.DockerClient(base_url="unix://var/run/docker.sock", timeout=2)
                    client.ping()
                    settings.DOCKER_HOSTS = {"localhost": "unix://var/run/docker.sock"}
                    logger.info("Auto-configured Docker collector to use local Docker socket (unix://var/run/docker.sock)")
            except Exception:
                try:
                    client = docker_lib.from_env(timeout=2)
                    client.ping()
                    base_url = client.api.base_url
                    settings.DOCKER_HOSTS = {"localhost": base_url}
                    logger.info("Auto-configured Docker collector to use default environment Docker socket: %s", base_url)
                except Exception:
                    pass

        if settings.DOCKER_HOSTS:
            await self._safe_start("docker_collector", self._start_docker)
        else:
            logger.info("Docker collector disabled (no DOCKER_HOSTS configured and local Docker socket unreachable)")

        if settings.PROMETHEUS_URL:
            await self._safe_start("prometheus_collector", self._start_prometheus)
        else:
            logger.info("Prometheus collector disabled (no PROMETHEUS_URL configured)")

        if settings.LONGHORN_API_URL:
            await self._safe_start("longhorn_collector", self._start_longhorn)
        else:
            logger.info("Longhorn collector disabled (no LONGHORN_API_URL configured)")

        self._tasks.append(
            asyncio.create_task(
                self._rca_engine.run_forever(), name="rca_engine"
            )
        )

    async def _safe_start(self, name: str, factory):
        try:
            await factory()
        except Exception as exc:
            logger.warning("Could not start %s (%s) — skipping", name, exc)

    async def _start_k8s(self):
        from app.collectors.k8s_collector import KubernetesCollector
        col = KubernetesCollector()
        self._tasks.append(
            asyncio.create_task(col.run_forever(), name="k8s_collector")
        )

    async def _start_docker(self):
        from app.collectors.docker_collector import DockerCollector
        col = DockerCollector()
        self._tasks.append(
            asyncio.create_task(col.run_forever(), name="docker_collector")
        )

    async def _start_prometheus(self):
        from app.collectors.prometheus_collector import PrometheusCollector
        col = PrometheusCollector()
        self._tasks.append(
            asyncio.create_task(col.run_forever(), name="prom_collector")
        )

    async def _start_longhorn(self):
        from app.collectors.longhorn_collector import LonghornCollector
        col = LonghornCollector()
        self._tasks.append(
            asyncio.create_task(col.run_forever(), name="longhorn_collector")
        )

    # ------------------------------------------------------------------
    # Scheduler
    # ------------------------------------------------------------------

    def _start_scheduler(self):
        self._scheduler.add_job(
            run_retention,
            "interval",
            hours=1,
            id="retention_job",
            max_instances=1,
            misfire_grace_time=60,
        )
        self._scheduler.start()
        logger.info("APScheduler started (retention every 1 h)")


# Module-level singleton — imported by main.py lifespan
services = BackgroundServices()
