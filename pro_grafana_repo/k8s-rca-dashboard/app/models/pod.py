"""
Current-state tables for Pods and their Containers.

`Pod` is upserted keyed by `uid` (stable across the pod's lifetime).
`ContainerStatus` rows are children of a pod and are replaced wholesale
on every poll (a pod typically has 1-4 containers, so this is cheap).
"""

from sqlalchemy import (
    Column, String, Float, Integer, DateTime, JSON, ForeignKey, Boolean
)
from sqlalchemy.orm import relationship
from datetime import datetime, timezone

from app.core.database import Base


class Pod(Base):
    __tablename__ = "pods"

    uid = Column(String, primary_key=True)
    name = Column(String, index=True)
    namespace = Column(String, index=True)
    node_name = Column(String, index=True, nullable=True)

    phase = Column(String, default="Unknown")     # Pending/Running/Succeeded/Failed/Unknown
    status_reason = Column(String, default="")    # e.g. Evicted, NodeLost
    pod_ip = Column(String, default="")
    qos_class = Column(String, default="")

    owner_kind = Column(String, default="")       # Deployment/StatefulSet/DaemonSet/ReplicaSet/Job
    owner_name = Column(String, default="")
    deployment = Column(String, default="")
    statefulset = Column(String, default="")
    daemonset = Column(String, default="")
    replicaset = Column(String, default="")

    restart_count = Column(Integer, default=0)    # sum across containers

    pvc_names = Column(JSON, default=list)        # ["data-kafka-0", ...]
    longhorn_volumes = Column(JSON, default=list) # resolved volume names for those PVCs

    labels = Column(JSON, default=dict)
    conditions = Column(JSON, default=dict)       # PodScheduled, Initialized, Ready, ContainersReady
    node_selector = Column(JSON, default=dict)
    tolerations = Column(JSON, default=list)

    ready = Column(Boolean, default=False)

    created_at = Column(DateTime, nullable=True)      # pod creationTimestamp from k8s
    first_seen = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_updated = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    containers = relationship("ContainerStatus", back_populates="pod",
                               cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "uid": self.uid,
            "name": self.name,
            "namespace": self.namespace,
            "node_name": self.node_name,
            "phase": self.phase,
            "status_reason": self.status_reason,
            "pod_ip": self.pod_ip,
            "qos_class": self.qos_class,
            "owner_kind": self.owner_kind,
            "owner_name": self.owner_name,
            "deployment": self.deployment,
            "statefulset": self.statefulset,
            "daemonset": self.daemonset,
            "replicaset": self.replicaset,
            "restart_count": self.restart_count,
            "pvc_names": self.pvc_names or [],
            "longhorn_volumes": self.longhorn_volumes or [],
            "conditions": self.conditions or {},
            "ready": self.ready,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_updated": self.last_updated.isoformat() if self.last_updated else None,
            "containers": [c.to_dict() for c in self.containers],
        }


class ContainerStatus(Base):
    __tablename__ = "container_statuses"

    id = Column(Integer, primary_key=True, autoincrement=True)
    pod_uid = Column(String, ForeignKey("pods.uid", ondelete="CASCADE"), index=True)
    container_name = Column(String)
    image = Column(String, default="")

    ready = Column(Boolean, default=False)
    started = Column(Boolean, default=False)
    restart_count = Column(Integer, default=0)

    state = Column(String, default="")            # running/waiting/terminated
    state_reason = Column(String, default="")      # CrashLoopBackOff, OOMKilled, ContainerCreating...
    state_message = Column(String, default="")

    last_state = Column(String, default="")
    last_state_reason = Column(String, default="")  # e.g. OOMKilled
    last_exit_code = Column(Integer, nullable=True)
    last_finished_at = Column(DateTime, nullable=True)

    cpu_limit_millicores = Column(Float, default=0.0)
    cpu_request_millicores = Column(Float, default=0.0)
    mem_limit_bytes = Column(Float, default=0.0)
    mem_request_bytes = Column(Float, default=0.0)

    liveness_probe_failures = Column(Integer, default=0)
    readiness_probe_failures = Column(Integer, default=0)

    last_updated = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    pod = relationship("Pod", back_populates="containers")

    def to_dict(self):
        return {
            "container_name": self.container_name,
            "image": self.image,
            "ready": self.ready,
            "restart_count": self.restart_count,
            "state": self.state,
            "state_reason": self.state_reason,
            "last_state": self.last_state,
            "last_state_reason": self.last_state_reason,
            "last_exit_code": self.last_exit_code,
            "last_finished_at": self.last_finished_at.isoformat() if self.last_finished_at else None,
            "cpu_limit_millicores": self.cpu_limit_millicores,
            "cpu_request_millicores": self.cpu_request_millicores,
            "mem_limit_bytes": self.mem_limit_bytes,
            "mem_request_bytes": self.mem_request_bytes,
            "liveness_probe_failures": self.liveness_probe_failures,
            "readiness_probe_failures": self.readiness_probe_failures,
        }
