"""
Root Cause records produced by the rule-based correlation engine
(app/rca/engine.py, built in a later step).

Each row is one "verdict" tied to a specific pod incident, with the raw
evidence (metric snapshots that triggered the rule) stored as JSON so a
future LLM layer can turn it into a natural-language explanation without
needing to re-query every source table.
"""

from sqlalchemy import Column, String, Integer, DateTime, JSON, Index
from datetime import datetime, timezone

from app.core.database import Base


class RootCauseRecord(Base):
    __tablename__ = "root_cause_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)

    pod_uid = Column(String, index=True)
    pod_name = Column(String)
    namespace = Column(String, index=True)
    node_name = Column(String, index=True)

    category = Column(String, index=True)     # cpu/memory/disk/network/longhorn/scheduling
    reason_code = Column(String, index=True)   # CPU_SATURATION, MEMORY_PRESSURE, ...
    severity = Column(String, default="warning")   # critical/warning/info

    short_reason = Column(String, default="")   # short label, e.g. "CPU Saturation"
    explanation = Column(String, default="")    # human-readable sentence(s)
    evidence = Column(JSON, default=dict)       # {"cpu_pct": 98.2, "restart_count_delta": 1, ...}

    # Placeholder fields for the future AI narrative layer described in the brief
    ai_explanation = Column(String, nullable=True)
    ai_generated_at = Column(DateTime, nullable=True)

    resolved = Column(Integer, default=0)   # 0/1 - whether pod has since stabilized

    __table_args__ = (
        Index("ix_rca_pod_ts", "pod_uid", "timestamp"),
    )
