"""
Alerts surfaced in the dashboard's Alerts panel (Critical / Warning /
Healthy / Historical). Alerts are usually created 1:1 alongside a
RootCauseRecord, but can also be raised directly by collectors for
conditions that don't need full RCA correlation (e.g. "Node NotReady").
"""

from sqlalchemy import Column, String, Integer, DateTime, Boolean, ForeignKey, Index
from datetime import datetime, timezone

from app.core.database import Base


class Alert(Base):
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)

    severity = Column(String, index=True)     # critical/warning/healthy
    source = Column(String, default="rca")     # rca/k8s/docker/longhorn/prometheus

    pod_name = Column(String, default="", index=True)
    namespace = Column(String, default="")
    node_name = Column(String, default="", index=True)

    title = Column(String)
    message = Column(String, default="")

    root_cause_id = Column(Integer, ForeignKey("root_cause_records.id"), nullable=True)

    acknowledged = Column(Boolean, default=False)
    resolved = Column(Boolean, default=False)
    resolved_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_alert_severity_ts", "severity", "timestamp"),
    )
