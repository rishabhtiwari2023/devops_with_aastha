"""
Kubernetes Events (as emitted by the API server / kubelet / controllers)
and a derived RestartHistory table that the collector writes to whenever
it detects a container restart count has increased since the last poll.

RestartHistory is what the RCA engine keys most of its correlation off,
since "restart count increased" is present in nearly every rule.
"""

from sqlalchemy import Column, String, Integer, DateTime, Index
from datetime import datetime, timezone

from app.core.database import Base


class K8sEvent(Base):
    __tablename__ = "k8s_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_uid = Column(String, index=True)          # k8s event uid (dedup key)
    namespace = Column(String, index=True)

    involved_object_kind = Column(String, default="")   # Pod/Node/PersistentVolumeClaim...
    involved_object_name = Column(String, index=True)
    node_name = Column(String, default="", index=True)

    reason = Column(String, index=True)     # Evicted, FailedScheduling, Unhealthy, BackOff...
    message = Column(String, default="")
    event_type = Column(String, default="Normal")   # Normal / Warning
    source_component = Column(String, default="")

    count = Column(Integer, default=1)
    first_seen = Column(DateTime, nullable=True)
    last_seen = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)

    __table_args__ = (
        Index("ix_event_obj_ts", "involved_object_name", "last_seen"),
    )


class RestartHistory(Base):
    __tablename__ = "restart_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)

    pod_uid = Column(String, index=True)
    pod_name = Column(String)
    namespace = Column(String, index=True)
    node_name = Column(String, index=True)
    container_name = Column(String)

    restart_count = Column(Integer)          # new total after this restart
    previous_restart_count = Column(Integer)

    last_state = Column(String, default="")       # e.g. terminated
    last_state_reason = Column(String, default="")  # OOMKilled/Error/Completed
    exit_code = Column(Integer, nullable=True)

    __table_args__ = (
        Index("ix_restart_pod_ts", "pod_uid", "timestamp"),
    )
