"""
Time-series data retention job.

Deletes rows that fall outside the configured retention windows so the
SQLite file doesn't grow unbounded.  Called hourly by the APScheduler
instance in app/background.py.
"""

import logging
from datetime import datetime, timezone, timedelta

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.metrics import PodMetric, NodeMetric, DockerMetric
from app.models.longhorn import LonghornVolumeMetric
from app.models.events import K8sEvent, RestartHistory
from app.models.rca import RootCauseRecord
from app.models.alerts import Alert

logger = logging.getLogger("rca.retention")


def _cutoff(hours: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=hours)


def run_retention():
    """Delete rows outside the retention windows and log the counts."""
    db = SessionLocal()
    try:
        metrics_cutoff = _cutoff(settings.METRICS_RETENTION_HOURS)
        events_cutoff = _cutoff(settings.EVENTS_RETENTION_HOURS)
        deleted: dict[str, int] = {}

        # Short-retention: raw metric time series
        for Model in (PodMetric, NodeMetric, DockerMetric, LonghornVolumeMetric):
            n = db.query(Model).filter(Model.timestamp < metrics_cutoff).delete(
                synchronize_session=False
            )
            deleted[Model.__tablename__] = n

        # Longer-retention: events, restart history, RCA records, alerts
        for Model in (RestartHistory,):
            n = db.query(Model).filter(Model.timestamp < events_cutoff).delete(
                synchronize_session=False
            )
            deleted[Model.__tablename__] = n

        # K8sEvent uses `last_seen` as the age column
        n = db.query(K8sEvent).filter(K8sEvent.last_seen < events_cutoff).delete(
            synchronize_session=False
        )
        deleted[K8sEvent.__tablename__] = n

        for Model in (RootCauseRecord, Alert):
            n = db.query(Model).filter(Model.timestamp < events_cutoff).delete(
                synchronize_session=False
            )
            deleted[Model.__tablename__] = n

        db.commit()
        logger.info("Retention pass complete: %s", deleted)
    except Exception:
        logger.exception("Retention job failed")
        db.rollback()
    finally:
        db.close()
