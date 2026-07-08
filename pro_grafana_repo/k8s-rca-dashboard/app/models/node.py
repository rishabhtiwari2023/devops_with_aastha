"""
Current-state table for cluster Nodes.

This table is *upserted* on every Kubernetes poll (not appended), since we
only need the latest known state of each node. Historical node resource
usage lives in `NodeMetric` (see models/metrics.py).
"""

from sqlalchemy import Column, String, Float, Integer, Boolean, DateTime, JSON
from datetime import datetime, timezone

from app.core.database import Base


class Node(Base):
    __tablename__ = "nodes"

    name = Column(String, primary_key=True)              # e.g. server-1
    status = Column(String, default="Unknown")            # Ready / NotReady / Unknown
    roles = Column(String, default="")                     # control-plane,worker etc (comma joined)
    kubelet_version = Column(String, default="")
    os_image = Column(String, default="")
    architecture = Column(String, default="")
    container_runtime = Column(String, default="")

    cpu_capacity_millicores = Column(Float, default=0.0)
    cpu_allocatable_millicores = Column(Float, default=0.0)
    mem_capacity_bytes = Column(Float, default=0.0)
    mem_allocatable_bytes = Column(Float, default=0.0)

    pod_count = Column(Integer, default=0)

    # Node conditions: MemoryPressure, DiskPressure, PIDPressure, Ready, NetworkUnavailable
    conditions = Column(JSON, default=dict)   # {"Ready": "True", "DiskPressure": "False", ...}
    taints = Column(JSON, default=list)       # [{"key":..., "value":..., "effect":...}, ...]

    schedulable = Column(Boolean, default=True)   # not cordoned

    first_seen = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_updated = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {
            "name": self.name,
            "status": self.status,
            "roles": self.roles,
            "kubelet_version": self.kubelet_version,
            "os_image": self.os_image,
            "architecture": self.architecture,
            "container_runtime": self.container_runtime,
            "cpu_capacity_millicores": self.cpu_capacity_millicores,
            "cpu_allocatable_millicores": self.cpu_allocatable_millicores,
            "mem_capacity_bytes": self.mem_capacity_bytes,
            "mem_allocatable_bytes": self.mem_allocatable_bytes,
            "pod_count": self.pod_count,
            "conditions": self.conditions or {},
            "taints": self.taints or [],
            "schedulable": self.schedulable,
            "last_updated": self.last_updated.isoformat() if self.last_updated else None,
        }
