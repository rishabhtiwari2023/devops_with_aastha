"""
Time-series metric tables.

Unlike Node/Pod (current-state, upserted), these are append-only and
form the historical record used for trend graphs, heatmaps and the RCA
correlation engine's "look-back" window. A retention job (see
app/core/retention.py, added in a later step) prunes old rows.
"""

from sqlalchemy import Column, String, Float, Integer, DateTime, Index
from datetime import datetime, timezone

from app.core.database import Base


class PodMetric(Base):
    """CPU/Memory usage for a pod, sourced from Prometheus (kube-state-metrics /
    cadvisor). One row per pod per poll cycle."""
    __tablename__ = "pod_metrics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    pod_uid = Column(String, index=True)
    pod_name = Column(String)
    namespace = Column(String, index=True)
    node_name = Column(String, index=True)

    cpu_usage_millicores = Column(Float, default=0.0)
    cpu_limit_millicores = Column(Float, default=0.0)
    cpu_request_millicores = Column(Float, default=0.0)
    cpu_pct_of_limit = Column(Float, default=0.0)   # usage / limit * 100

    mem_usage_bytes = Column(Float, default=0.0)
    mem_limit_bytes = Column(Float, default=0.0)
    mem_request_bytes = Column(Float, default=0.0)
    mem_pct_of_limit = Column(Float, default=0.0)

    __table_args__ = (
        Index("ix_podmetric_pod_ts", "pod_uid", "timestamp"),
    )


class NodeMetric(Base):
    """Node-level resource usage, sourced from Prometheus node_exporter."""
    __tablename__ = "node_metrics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    node_name = Column(String, index=True)

    cpu_pct = Column(Float, default=0.0)
    mem_pct = Column(Float, default=0.0)
    mem_used_bytes = Column(Float, default=0.0)
    mem_total_bytes = Column(Float, default=0.0)

    disk_used_bytes = Column(Float, default=0.0)
    disk_total_bytes = Column(Float, default=0.0)
    disk_pct = Column(Float, default=0.0)
    filesystem_pct = Column(Float, default=0.0)

    load1 = Column(Float, default=0.0)
    load5 = Column(Float, default=0.0)
    load15 = Column(Float, default=0.0)

    memory_pressure = Column(Float, default=0.0)   # 0/1 boolean-as-float for easy charting
    disk_pressure = Column(Float, default=0.0)
    pid_pressure = Column(Float, default=0.0)

    __table_args__ = (
        Index("ix_nodemetric_node_ts", "node_name", "timestamp"),
    )


class DockerMetric(Base):
    """Per-container network + block IO, sourced directly from the Docker
    Engine API (since Prometheus/cAdvisor in this environment doesn't expose
    pod-level network stats). Joined back to pods via Docker labels."""
    __tablename__ = "docker_metrics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)

    pod_uid = Column(String, index=True)
    pod_name = Column(String)
    namespace = Column(String, index=True)
    container_name = Column(String)
    container_id = Column(String)
    node_name = Column(String, index=True)

    net_rx_bytes = Column(Float, default=0.0)
    net_tx_bytes = Column(Float, default=0.0)
    net_rx_packets = Column(Float, default=0.0)
    net_tx_packets = Column(Float, default=0.0)

    # Rates (computed as delta / delta_t between consecutive polls)
    net_rx_bytes_per_sec = Column(Float, default=0.0)
    net_tx_bytes_per_sec = Column(Float, default=0.0)

    blk_read_bytes = Column(Float, default=0.0)
    blk_write_bytes = Column(Float, default=0.0)
    blk_read_bytes_per_sec = Column(Float, default=0.0)
    blk_write_bytes_per_sec = Column(Float, default=0.0)

    iops = Column(Float, default=0.0)   # (read_ops + write_ops) / delta_t

    __table_args__ = (
        Index("ix_dockermetric_pod_ts", "pod_uid", "timestamp"),
    )
