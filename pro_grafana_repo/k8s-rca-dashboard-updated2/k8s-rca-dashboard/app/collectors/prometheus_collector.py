"""
Prometheus collector.

Runs a batch of PromQL instant queries every `PROM_POLL_INTERVAL` seconds
and turns the results into PodMetric / NodeMetric rows.

Assumes a fairly standard kube-prometheus-stack style setup:
  - cadvisor metrics (container_cpu_usage_seconds_total,
    container_memory_working_set_bytes) via the kubelet /metrics/cadvisor
    endpoint
  - kube-state-metrics (kube_pod_container_resource_limits/requests,
    kube_node_status_condition)
  - node_exporter (node_cpu_seconds_total, node_memory_*, node_filesystem_*,
    node_load1/5/15)

Pod-level queries are joined back to our own `pods` table by
(namespace, pod name) to resolve `pod_uid`, since PromQL results don't
carry Kubernetes UIDs.
"""

import asyncio
import logging
from datetime import datetime, timezone

import aiohttp
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal, SessionLocalWrite
from app.collectors.prometheus_client import instant_query
from app.models.metrics import PodMetric, NodeMetric, DockerMetric
from app.models.pod import Pod
from app.models.node import Node

logger = logging.getLogger("rca.prometheus_collector")


# ----------------------------------------------------------------------
# PromQL queries
# ----------------------------------------------------------------------
Q_POD_CPU_USAGE_MILLICORES = (
    'sum by (namespace, pod) '
    '(rate(container_cpu_usage_seconds_total{container!="", container!="POD"}[2m])) * 1000'
)
Q_POD_CPU_LIMIT_CORES = 'kube_pod_container_resource_limits{resource="cpu"}'
Q_POD_CPU_REQUEST_CORES = 'kube_pod_container_resource_requests{resource="cpu"}'
Q_POD_MEM_USAGE_BYTES = (
    'sum by (namespace, pod) '
    '(container_memory_working_set_bytes{container!="", container!="POD"})'
)
Q_POD_MEM_LIMIT_BYTES = 'kube_pod_container_resource_limits{resource="memory"}'
Q_POD_MEM_REQUEST_BYTES = 'kube_pod_container_resource_requests{resource="memory"}'

# Pod Network & Disk queries (derived from cAdvisor/Prometheus)
Q_POD_NET_RX_RATE = 'sum by (namespace, pod) (rate(container_network_receive_bytes_total{pod!=""}[5m]))'
Q_POD_NET_TX_RATE = 'sum by (namespace, pod) (rate(container_network_transmit_bytes_total{pod!=""}[5m]))'
Q_POD_DISK_READ_RATE = 'sum by (namespace, pod) (rate(container_blkio_device_usage_total{operation="Read", pod!=""}[5m]))'
Q_POD_DISK_WRITE_RATE = 'sum by (namespace, pod) (rate(container_blkio_device_usage_total{operation="Write", pod!=""}[5m]))'


Q_NODE_CPU_IDLE_RATIO = 'avg by ({label}) (rate(node_cpu_seconds_total{{mode="idle"}}[2m]))'
Q_NODE_MEM_TOTAL = 'node_memory_MemTotal_bytes'
Q_NODE_MEM_AVAILABLE = 'node_memory_MemAvailable_bytes'
Q_NODE_DISK_SIZE = 'node_filesystem_size_bytes{mountpoint="/"}'
Q_NODE_DISK_AVAIL = 'node_filesystem_avail_bytes{mountpoint="/"}'
Q_NODE_LOAD1 = 'node_load1'
Q_NODE_LOAD5 = 'node_load5'
Q_NODE_LOAD15 = 'node_load15'
Q_NODE_CONDITION = 'kube_node_status_condition{{condition="{cond}", status="true"}}'


class PrometheusCollector:
    async def collect_once(self):
        async with aiohttp.ClientSession() as session:
            pod_task = self._collect_pod_metrics(session)
            node_task = self._collect_node_metrics(session)
            pod_docker_task = self._collect_pod_docker_metrics(session)
            pod_rows, node_rows, pod_docker_rows = await asyncio.gather(
                pod_task, node_task, pod_docker_task
            )

        db = SessionLocalWrite()
        try:
            self._write_pod_metrics(db, pod_rows)
            self._write_node_metrics(db, node_rows)
            self._write_pod_docker_metrics(db, pod_docker_rows)
            db.commit()
        except Exception:
            logger.exception("Failed writing Prometheus metrics to DB")
            db.rollback()
        finally:
            db.close()

    async def run_forever(self):
        logger.info("Prometheus collector starting (interval=%ss)", settings.PROM_POLL_INTERVAL)
        while True:
            start = datetime.now(timezone.utc)
            await self.collect_once()
            elapsed = (datetime.now(timezone.utc) - start).total_seconds()
            await asyncio.sleep(max(0.0, settings.PROM_POLL_INTERVAL - elapsed))

    # ------------------------------------------------------------------
    # Pod metrics
    # ------------------------------------------------------------------
    async def _collect_pod_metrics(self, session: aiohttp.ClientSession) -> dict:
        usage, cpu_limit, cpu_req, mem_usage, mem_limit, mem_req = await asyncio.gather(
            instant_query(session, Q_POD_CPU_USAGE_MILLICORES),
            instant_query(session, Q_POD_CPU_LIMIT_CORES),
            instant_query(session, Q_POD_CPU_REQUEST_CORES),
            instant_query(session, Q_POD_MEM_USAGE_BYTES),
            instant_query(session, Q_POD_MEM_LIMIT_BYTES),
            instant_query(session, Q_POD_MEM_REQUEST_BYTES),
        )

        usage_d = _vector_to_dict(usage, ("namespace", "pod"))
        cpu_limit_d = _vector_to_dict(cpu_limit, ("namespace", "pod"), sum_duplicates=True)
        cpu_req_d = _vector_to_dict(cpu_req, ("namespace", "pod"), sum_duplicates=True)
        mem_usage_d = _vector_to_dict(mem_usage, ("namespace", "pod"))
        mem_limit_d = _vector_to_dict(mem_limit, ("namespace", "pod"), sum_duplicates=True)
        mem_req_d = _vector_to_dict(mem_req, ("namespace", "pod"), sum_duplicates=True)

        all_keys = set(usage_d) | set(mem_usage_d) | set(cpu_limit_d) | set(mem_limit_d)
        merged = {}
        for key in all_keys:
            cpu_usage_mc = usage_d.get(key, 0.0)
            cpu_limit_mc = cpu_limit_d.get(key, 0.0) * 1000.0   # cores -> millicores
            cpu_req_mc = cpu_req_d.get(key, 0.0) * 1000.0
            mem_usage_b = mem_usage_d.get(key, 0.0)
            mem_limit_b = mem_limit_d.get(key, 0.0)
            mem_req_b = mem_req_d.get(key, 0.0)

            merged[key] = {
                "cpu_usage_millicores": cpu_usage_mc,
                "cpu_limit_millicores": cpu_limit_mc,
                "cpu_request_millicores": cpu_req_mc,
                "cpu_pct_of_limit": (cpu_usage_mc / cpu_limit_mc * 100.0) if cpu_limit_mc else 0.0,
                "mem_usage_bytes": mem_usage_b,
                "mem_limit_bytes": mem_limit_b,
                "mem_request_bytes": mem_req_b,
                "mem_pct_of_limit": (mem_usage_b / mem_limit_b * 100.0) if mem_limit_b else 0.0,
            }
        return merged

    def _write_pod_metrics(self, db: Session, pod_rows: dict):
        # Resolve (namespace, pod name) -> (pod_uid, node_name) via our own table using read-only session
        read_db = SessionLocal()
        try:
            pods = read_db.query(Pod.name, Pod.namespace, Pod.uid, Pod.node_name).all()
        finally:
            read_db.close()
        lookup = {(ns, name): (uid, node) for name, ns, uid, node in pods}

        for (namespace, pod_name), metrics in pod_rows.items():
            uid, node_name = lookup.get((namespace, pod_name), ("", ""))
            if not uid:
                logger.warning("Could not resolve pod_uid for pod %s/%s. Metrics will not map to a pod in DB.", namespace, pod_name)
            db.add(PodMetric(
                pod_uid=uid,
                pod_name=pod_name,
                namespace=namespace,
                node_name=node_name or "",
                **metrics,
            ))
        db.flush()

    # ------------------------------------------------------------------
    # Node metrics
    # ------------------------------------------------------------------
    async def _collect_node_metrics(self, session: aiohttp.ClientSession) -> dict:
        label = settings.PROM_NODE_LABEL
        
        # Test query to check if the configured label returns metrics.
        # Fall back to "instance" if "node" returns empty or fails.
        cpu_idle = await instant_query(session, Q_NODE_CPU_IDLE_RATIO.format(label=label))
        if not cpu_idle and label == "node":
            logger.info("No node metrics found with label 'node'. Trying fallback label 'instance'...")
            label = "instance"
            cpu_idle = await instant_query(session, Q_NODE_CPU_IDLE_RATIO.format(label=label))

        mem_total, mem_avail, disk_size, disk_avail, load1, load5, load15, \
            mem_pressure, disk_pressure, pid_pressure = await asyncio.gather(
                instant_query(session, Q_NODE_MEM_TOTAL),
                instant_query(session, Q_NODE_MEM_AVAILABLE),
                instant_query(session, Q_NODE_DISK_SIZE),
                instant_query(session, Q_NODE_DISK_AVAIL),
                instant_query(session, Q_NODE_LOAD1),
                instant_query(session, Q_NODE_LOAD5),
                instant_query(session, Q_NODE_LOAD15),
                instant_query(session, Q_NODE_CONDITION.format(cond="MemoryPressure")),
                instant_query(session, Q_NODE_CONDITION.format(cond="DiskPressure")),
                instant_query(session, Q_NODE_CONDITION.format(cond="PIDPressure")),
            )

        cpu_idle_d = _vector_to_dict(cpu_idle, (label,))
        mem_total_d = _vector_to_dict(mem_total, (label,))
        mem_avail_d = _vector_to_dict(mem_avail, (label,))
        disk_size_d = _vector_to_dict(disk_size, (label,))
        disk_avail_d = _vector_to_dict(disk_avail, (label,))
        load1_d = _vector_to_dict(load1, (label,))
        load5_d = _vector_to_dict(load5, (label,))
        load15_d = _vector_to_dict(load15, (label,))
        # kube_node_status_condition carries the node name under "node" regardless
        mem_pressure_d = _vector_to_dict(mem_pressure, ("node",))
        disk_pressure_d = _vector_to_dict(disk_pressure, ("node",))
        pid_pressure_d = _vector_to_dict(pid_pressure, ("node",))

        db = SessionLocal()
        try:
            db_nodes = {n.name for n in db.query(Node.name).all()}
        except Exception:
            db_nodes = set()
        finally:
            db.close()

        node_keys = set(mem_total_d) | set(cpu_idle_d) | set(disk_size_d)
        merged = {}
        for (raw_key,) in [(k,) for k in node_keys]:
            node_name = settings.PROM_INSTANCE_TO_NODE.get(raw_key)
            if not node_name:
                # Strip port if present (e.g. "desktop-control-plane:9100" -> "desktop-control-plane")
                stripped = raw_key.split(":")[0]
                
                # Check IP mapping first
                from app.core.config import NODE_IP_TO_NAME
                node_name = NODE_IP_TO_NAME.get(stripped)
                
                if not node_name:
                    if stripped in db_nodes:
                        node_name = stripped
                    else:
                        # Case insensitive match fallback
                        for db_node in db_nodes:
                            if stripped.lower() == db_node.lower():
                                node_name = db_node
                                break
                        else:
                            # Single-node cluster fallback: if there is only 1 node in the database, map it to that node
                            if len(db_nodes) == 1:
                                node_name = list(db_nodes)[0]
                            else:
                                node_name = stripped
                                logger.warning("Could not map Prometheus node key '%s' to any K8s node in DB. Metrics will write under raw name '%s'.", raw_key, stripped)

            total = mem_total_d.get(raw_key, 0.0)
            avail = mem_avail_d.get(raw_key, 0.0)
            dsize = disk_size_d.get(raw_key, 0.0)
            davail = disk_avail_d.get(raw_key, 0.0)
            idle_ratio = cpu_idle_d.get(raw_key, 1.0)

            merged[node_name] = {
                "cpu_pct": max(0.0, (1.0 - idle_ratio) * 100.0),
                "mem_pct": ((total - avail) / total * 100.0) if total else 0.0,
                "mem_used_bytes": total - avail,
                "mem_total_bytes": total,
                "disk_used_bytes": dsize - davail,
                "disk_total_bytes": dsize,
                "disk_pct": ((dsize - davail) / dsize * 100.0) if dsize else 0.0,
                "filesystem_pct": ((dsize - davail) / dsize * 100.0) if dsize else 0.0,
                "load1": load1_d.get(raw_key, 0.0),
                "load5": load5_d.get(raw_key, 0.0),
                "load15": load15_d.get(raw_key, 0.0),
                "memory_pressure": mem_pressure_d.get(node_name, 0.0),
                "disk_pressure": disk_pressure_d.get(node_name, 0.0),
                "pid_pressure": pid_pressure_d.get(node_name, 0.0),
            }
        return merged

    def _write_node_metrics(self, db: Session, node_rows: dict):
        for node_name, metrics in node_rows.items():
            db.add(NodeMetric(node_name=node_name, **metrics))
        db.flush()

    async def _collect_pod_docker_metrics(self, session: aiohttp.ClientSession) -> dict:
        rx, tx, blk_read, blk_write = await asyncio.gather(
            instant_query(session, Q_POD_NET_RX_RATE),
            instant_query(session, Q_POD_NET_TX_RATE),
            instant_query(session, Q_POD_DISK_READ_RATE),
            instant_query(session, Q_POD_DISK_WRITE_RATE),
        )

        rx_d = _vector_to_dict(rx, ("namespace", "pod"))
        tx_d = _vector_to_dict(tx, ("namespace", "pod"))
        blk_read_d = _vector_to_dict(blk_read, ("namespace", "pod"))
        blk_write_d = _vector_to_dict(blk_write, ("namespace", "pod"))

        all_keys = set(rx_d) | set(tx_d) | set(blk_read_d) | set(blk_write_d)
        merged = {}
        for key in all_keys:
            merged[key] = {
                "net_rx_bytes_per_sec": rx_d.get(key, 0.0),
                "net_tx_bytes_per_sec": tx_d.get(key, 0.0),
                "blk_read_bytes_per_sec": blk_read_d.get(key, 0.0),
                "blk_write_bytes_per_sec": blk_write_d.get(key, 0.0),
                "iops": 0.0,
            }
        return merged

    def _write_pod_docker_metrics(self, db: Session, pod_docker_rows: dict):
        # Resolve (namespace, pod name) -> (pod_uid, node_name) via our own table using read-only session
        read_db = SessionLocal()
        try:
            pods = read_db.query(Pod.name, Pod.namespace, Pod.uid, Pod.node_name).all()
        finally:
            read_db.close()
        lookup = {(ns, name): (uid, node) for name, ns, uid, node in pods}

        for (namespace, pod_name), metrics in pod_docker_rows.items():
            uid, node_name = lookup.get((namespace, pod_name), ("", ""))
            if not uid:
                logger.warning("Could not resolve pod_uid for pod %s/%s in docker metrics. Metrics will not map to a pod in DB.", namespace, pod_name)
            db.add(DockerMetric(
                pod_uid=uid,
                pod_name=pod_name,
                namespace=namespace,
                container_name="main",
                container_id="prom-derived",
                node_name=node_name or "",
                **metrics,
            ))
        db.flush()



# ----------------------------------------------------------------------
# Pure parsing helpers
# ----------------------------------------------------------------------
def _vector_to_dict(vector: list[dict], label_keys: tuple, sum_duplicates: bool = False):
    """Turn a Prometheus instant-query result vector into a dict keyed by
    the requested label tuple (or a single label's bare value when
    label_keys has length 1), mapping to the float value.

    `sum_duplicates`: kube_pod_container_resource_limits/requests emits one
    series per *container*, so multiple rows can share the same
    (namespace, pod) key. When True, values are summed instead of the last
    one silently overwriting the others - this is what you want for a
    pod's total CPU/memory limit across all its containers.
    """
    result: dict = {}
    for series in vector:
        metric = series.get("metric", {})
        try:
            value = float(series["value"][1])
        except (KeyError, IndexError, ValueError, TypeError):
            continue

        if len(label_keys) == 1:
            lbl = label_keys[0]
            key = metric.get(lbl)
            if key is None and lbl == "node":
                key = metric.get("instance") or metric.get("kubernetes_node")
            if key is None:
                continue
        else:
            key = tuple(metric.get(k, "") for k in label_keys)
            if any(part == "" for part in key):
                continue

        if key in result and sum_duplicates:
            result[key] += value
        else:
            result[key] = value
    return result
