"""
Longhorn storage metrics, sourced from the Longhorn Manager REST API.

Append-only time series so we can see, e.g., "replica started rebuilding
at 14:03, PVC detached at 14:05" on the timeline view.
"""

from sqlalchemy import Column, String, Float, Integer, Boolean, DateTime, JSON, Index
from datetime import datetime, timezone

from app.core.database import Base


class LonghornVolumeMetric(Base):
    __tablename__ = "longhorn_volume_metrics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)

    volume_name = Column(String, index=True)
    pvc_name = Column(String, index=True)
    pv_name = Column(String, default="")
    namespace = Column(String, default="")

    state = Column(String, default="")          # attached/detached/creating/faulted
    robustness = Column(String, default="")      # healthy/degraded/faulted
    attached_node = Column(String, default="")

    engine_name = Column(String, default="")
    engine_state = Column(String, default="")

    replica_count = Column(Integer, default=0)
    replica_states = Column(JSON, default=list)  # [{"name":..., "node":..., "state":...}]
    rebuild_in_progress = Column(Boolean, default=False)

    scheduled_size_bytes = Column(Float, default=0.0)
    actual_size_bytes = Column(Float, default=0.0)
    available_space_bytes = Column(Float, default=0.0)

    read_iops = Column(Float, default=0.0)
    write_iops = Column(Float, default=0.0)
    read_throughput_bytes_per_sec = Column(Float, default=0.0)
    write_throughput_bytes_per_sec = Column(Float, default=0.0)
    read_latency_ms = Column(Float, default=0.0)
    write_latency_ms = Column(Float, default=0.0)

    __table_args__ = (
        Index("ix_longhorn_vol_ts", "volume_name", "timestamp"),
    )
