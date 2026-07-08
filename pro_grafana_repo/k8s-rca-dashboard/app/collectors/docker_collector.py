"""
Docker collector.

Prometheus/cAdvisor in this environment does not expose pod-level network
statistics, so per the brief this collector talks directly to each node's
Docker Engine API for the metrics Prometheus can't give us:

  - Network RX/TX bytes + packets
  - Block (disk) read/write bytes
  - IOPS (derived from io_serviced_recursive)

Containers are mapped back to Kubernetes pods using the standard
CRI/dockershim labels every kubelet attaches to a container:

  io.kubernetes.pod.name
  io.kubernetes.pod.namespace
  io.kubernetes.pod.uid
  io.kubernetes.container.name

The Docker "pause"/sandbox container (container name "POD") carries the
pod's network namespace but isn't a real workload container, so its
labels are used only to confirm pod identity; we still record its stats
under its own container_name since its RX/TX is often what
`kubectl top` style tools attribute to the pod as a whole. Real workload
containers are recorded individually so per-container IO is visible too.

Docker's stats API returns *cumulative* counters, so absolute values are
stored alongside a computed per-second *rate* (bytes delta / time delta
between this poll and the previous one for the same container ID). The
first poll for a brand-new container has no previous sample, so its rate
is reported as 0 until the second poll.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone

from docker.errors import DockerException, NotFound
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.collectors.docker_client import get_docker_client
from app.models.metrics import DockerMetric

logger = logging.getLogger("rca.docker_collector")

_POD_NAME_LABEL = "io.kubernetes.pod.name"
_POD_NAMESPACE_LABEL = "io.kubernetes.pod.namespace"
_POD_UID_LABEL = "io.kubernetes.pod.uid"
_CONTAINER_NAME_LABEL = "io.kubernetes.container.name"


class DockerCollector:
    def __init__(self):
        # container_id -> {"ts": epoch_seconds, "net_rx":, "net_tx":, "blk_read":,
        #                   "blk_write":, "read_ops":, "write_ops":}
        self._prev: dict[str, dict] = {}

    # ------------------------------------------------------------------
    async def collect_once(self):
        db = SessionLocal()
        try:
            for node_name in settings.DOCKER_HOSTS:
                await asyncio.to_thread(self._collect_node, db, node_name)
            db.commit()
        except Exception:
            logger.exception("Unexpected error in Docker collector")
            db.rollback()
        finally:
            db.close()

    async def run_forever(self):
        logger.info("Docker collector starting (interval=%ss)", settings.DOCKER_POLL_INTERVAL)
        while True:
            start = datetime.now(timezone.utc)
            await self.collect_once()
            elapsed = (datetime.now(timezone.utc) - start).total_seconds()
            await asyncio.sleep(max(0.0, settings.DOCKER_POLL_INTERVAL - elapsed))

    # ------------------------------------------------------------------
    def _collect_node(self, db: Session, node_name: str):
        try:
            client = get_docker_client(node_name)
            containers = client.containers.list(filters={"status": "running"})
        except DockerException as e:
            logger.warning("Could not reach Docker daemon on %s: %s", node_name, e)
            return

        for container in containers:
            labels = container.labels or {}
            pod_name = labels.get(_POD_NAME_LABEL)
            namespace = labels.get(_POD_NAMESPACE_LABEL)
            pod_uid = labels.get(_POD_UID_LABEL, "")
            container_name = labels.get(_CONTAINER_NAME_LABEL, container.name)

            if not pod_name or not namespace:
                continue  # not a Kubernetes-managed container (e.g. system/infra container)

            try:
                stats = container.stats(stream=False)
            except (DockerException, NotFound) as e:
                logger.debug("stats() failed for %s: %s", container.short_id, e)
                continue

            net_rx, net_tx, net_rx_pkts, net_tx_pkts = _parse_network_stats(stats)
            blk_read, blk_write, read_ops, write_ops = _parse_blkio_stats(stats)

            now = time.time()
            prev = self._prev.get(container.id)
            net_rx_rate = net_tx_rate = blk_read_rate = blk_write_rate = iops = 0.0

            if prev:
                dt = max(now - prev["ts"], 0.001)
                net_rx_rate = _rate(net_rx, prev["net_rx"], dt)
                net_tx_rate = _rate(net_tx, prev["net_tx"], dt)
                blk_read_rate = _rate(blk_read, prev["blk_read"], dt)
                blk_write_rate = _rate(blk_write, prev["blk_write"], dt)
                iops = _rate(read_ops + write_ops, prev["read_ops"] + prev["write_ops"], dt)

            self._prev[container.id] = {
                "ts": now, "net_rx": net_rx, "net_tx": net_tx,
                "blk_read": blk_read, "blk_write": blk_write,
                "read_ops": read_ops, "write_ops": write_ops,
            }

            db.add(DockerMetric(
                pod_uid=pod_uid,
                pod_name=pod_name,
                namespace=namespace,
                container_name=container_name,
                container_id=container.id[:12],
                node_name=node_name,
                net_rx_bytes=net_rx,
                net_tx_bytes=net_tx,
                net_rx_packets=net_rx_pkts,
                net_tx_packets=net_tx_pkts,
                net_rx_bytes_per_sec=net_rx_rate,
                net_tx_bytes_per_sec=net_tx_rate,
                blk_read_bytes=blk_read,
                blk_write_bytes=blk_write,
                blk_read_bytes_per_sec=blk_read_rate,
                blk_write_bytes_per_sec=blk_write_rate,
                iops=iops,
            ))

        db.flush()
        self._evict_stale(containers)

    def _evict_stale(self, current_containers):
        """Drop cached previous-sample state for containers that no longer
        exist, so a restarted container with a new ID starts its rate
        calculation fresh instead of comparing against a dead container."""
        current_ids = {c.id for c in current_containers}
        for cid in list(self._prev.keys()):
            if cid not in current_ids:
                self._prev.pop(cid, None)


# ----------------------------------------------------------------------
# Pure parsing helpers (kept side-effect free and independently testable)
# ----------------------------------------------------------------------
def _parse_network_stats(stats: dict):
    """Docker's stats payload has a `networks` dict keyed by interface name
    (eth0, eth1, ...). Sum across all interfaces to get the container's
    total network IO."""
    networks = stats.get("networks") or {}
    rx_bytes = sum(iface.get("rx_bytes", 0) for iface in networks.values())
    tx_bytes = sum(iface.get("tx_bytes", 0) for iface in networks.values())
    rx_packets = sum(iface.get("rx_packets", 0) for iface in networks.values())
    tx_packets = sum(iface.get("tx_packets", 0) for iface in networks.values())
    return float(rx_bytes), float(tx_bytes), float(rx_packets), float(tx_packets)


def _parse_blkio_stats(stats: dict):
    """Docker's blkio_stats has per-device recursive counters, each entry
    tagged with an "op" of Read/Write/Sync/Async/Total. We sum Read and
    Write across all devices for both bytes and IO-op counts."""
    blkio = stats.get("blkio_stats") or {}

    def _sum_by_op(entries, op_name):
        return sum(e.get("value", 0) for e in entries if e.get("op", "").lower() == op_name)

    service_bytes = blkio.get("io_service_bytes_recursive") or []
    serviced_ops = blkio.get("io_serviced_recursive") or []

    read_bytes = _sum_by_op(service_bytes, "read")
    write_bytes = _sum_by_op(service_bytes, "write")
    read_ops = _sum_by_op(serviced_ops, "read")
    write_ops = _sum_by_op(serviced_ops, "write")

    return float(read_bytes), float(write_bytes), float(read_ops), float(write_ops)


def _rate(curr: float, prev: float, dt: float) -> float:
    """Counter-delta rate, floored at 0 to absorb counter resets
    (e.g. container's stats API occasionally resets after a Docker
    daemon restart)."""
    delta = curr - prev
    if delta < 0:
        return 0.0
    return delta / dt
