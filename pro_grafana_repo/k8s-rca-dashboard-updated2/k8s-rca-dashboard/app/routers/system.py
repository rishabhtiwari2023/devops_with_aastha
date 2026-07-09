"""
System / collector health endpoints.

NEW ROUTER (added on top of the original project).

GET /api/system/health    — status of every collector (K8s/Docker/Prometheus/
                            Longhorn) + the RCA engine, with a plain-English
                            reason whenever one of them is disabled or has
                            gone stale. This is what answers "why don't I see
                            CPU/RAM/Disk metrics?" without having to read logs.
GET /api/system/info      — app/version/db/retention info.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
import os

from app.core.config import settings
from app.core.database import get_db
from app.models.metrics import NodeMetric, PodMetric, DockerMetric
from app.models.longhorn import LonghornVolumeMetric
from app.models.node import Node
from app.models.pod import Pod
from app.models.rca import RootCauseRecord
from app.models.alerts import Alert
from app.models.events import K8sEvent, RestartHistory

router = APIRouter(prefix="/api/system", tags=["system"])

# How stale (seconds) a collector's most recent row can be before we call it
# "stale" instead of "ok", even though it's configured.
_STALE_AFTER_SECONDS = 120


def _latest_timestamp(db: Session, Model, ts_col: str = "timestamp"):
    col = getattr(Model, ts_col)
    return db.query(func.max(col)).scalar()


def _collector_status(db: Session, *, name: str, enabled: bool, Model,
                       disabled_reason: str, poll_interval: int,
                       ts_col: str = "timestamp"):
    """Build one collector's health block."""
    if not enabled:
        return {
            "name": name,
            "enabled": False,
            "status": "disabled",
            "reason": disabled_reason,
            "last_poll": None,
            "seconds_since_last_poll": None,
            "row_count": 0,
        }

    last_ts = _latest_timestamp(db, Model, ts_col)
    row_count = db.query(func.count()).select_from(Model).scalar() or 0

    if last_ts is None:
        return {
            "name": name,
            "enabled": True,
            "status": "no_data",
            "reason": (
                f"{name} is configured but no data has been collected yet. "
                f"It may still be starting up, or the endpoint may be "
                f"unreachable — check the server logs."
            ),
            "last_poll": None,
            "seconds_since_last_poll": None,
            "row_count": 0,
        }

    # SQLite stores naive UTC datetimes
    now = datetime.now(timezone.utc)
    last_ts_aware = last_ts if last_ts.tzinfo else last_ts.replace(tzinfo=timezone.utc)
    age_seconds = (now - last_ts_aware).total_seconds()
    stale = age_seconds > max(_STALE_AFTER_SECONDS, poll_interval * 4)

    return {
        "name": name,
        "enabled": True,
        "status": "stale" if stale else "ok",
        "reason": (
            f"No new data in {int(age_seconds)}s (expected every {poll_interval}s) — "
            f"the collector may have stopped or lost connectivity."
            if stale else "Collecting normally."
        ),
        "last_poll": last_ts_aware.isoformat(),
        "seconds_since_last_poll": round(age_seconds, 1),
        "row_count": row_count,
    }


@router.get("/health")
def system_health(db: Session = Depends(get_db)):
    """
    Per-collector health, so the dashboard (and its user) can see *why*
    a data source (e.g. CPU/RAM/Disk metrics from Prometheus) is empty
    instead of just silently showing blanks.
    """
    collectors = [
        _collector_status(
            db, name="kubernetes", enabled=True, Model=Pod,
            disabled_reason="", poll_interval=settings.K8S_POLL_INTERVAL,
            ts_col="last_updated",
        ),
        _collector_status(
            db, name="prometheus", enabled=bool(settings.PROMETHEUS_URL),
            Model=NodeMetric,
            disabled_reason=(
                "Prometheus collection is OFF because RCA_PROMETHEUS_URL is "
                "not set. This is why CPU %, Memory %, and Disk % are "
                "missing for nodes and pods. Set RCA_PROMETHEUS_URL (e.g. "
                "http://localhost:9090 after port-forwarding Prometheus) in "
                "your .env file and restart the server to enable it."
            ),
            poll_interval=settings.PROM_POLL_INTERVAL,
        ),
        _collector_status(
            db, name="docker", enabled=bool(settings.DOCKER_HOSTS),
            Model=DockerMetric,
            disabled_reason=(
                "Docker network/IO collection is OFF because RCA_DOCKER_HOSTS "
                "is empty. Network RX/TX and disk IOPS rankings will stay "
                "empty until you map node name -> Docker daemon URL in .env."
            ),
            poll_interval=settings.DOCKER_POLL_INTERVAL,
        ),
        _collector_status(
            db, name="longhorn", enabled=bool(settings.LONGHORN_API_URL),
            Model=LonghornVolumeMetric,
            disabled_reason=(
                "Longhorn collection is OFF because RCA_LONGHORN_API_URL is "
                "not set. Volume/replica health and disk-pressure RCA rules "
                "that depend on it won't fire until it's configured."
            ),
            poll_interval=settings.LONGHORN_POLL_INTERVAL,
        ),
    ]

    node_metrics_missing = collectors[1]["status"] in ("disabled", "no_data")
    overall_ok = all(c["status"] in ("ok", "disabled") for c in collectors)

    return {
        "overall_status": "ok" if overall_ok else "degraded",
        "cpu_ram_disk_metrics_available": not node_metrics_missing,
        "collectors": collectors,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/info")
def system_info(db: Session = Depends(get_db)):
    """App-level info: table row counts, retention config, DB file size."""
    db_size_bytes = 0
    if os.path.exists(settings.DB_PATH):
        db_size_bytes = os.path.getsize(settings.DB_PATH)

    counts = {
        "nodes": db.query(func.count()).select_from(Node).scalar() or 0,
        "pods": db.query(func.count()).select_from(Pod).scalar() or 0,
        "node_metrics": db.query(func.count()).select_from(NodeMetric).scalar() or 0,
        "pod_metrics": db.query(func.count()).select_from(PodMetric).scalar() or 0,
        "docker_metrics": db.query(func.count()).select_from(DockerMetric).scalar() or 0,
        "rca_records": db.query(func.count()).select_from(RootCauseRecord).scalar() or 0,
        "alerts": db.query(func.count()).select_from(Alert).scalar() or 0,
        "k8s_events": db.query(func.count()).select_from(K8sEvent).scalar() or 0,
        "restart_history": db.query(func.count()).select_from(RestartHistory).scalar() or 0,
    }

    return {
        "app_name": settings.APP_NAME,
        "db_path": settings.DB_PATH,
        "db_size_bytes": db_size_bytes,
        "metrics_retention_hours": settings.METRICS_RETENTION_HOURS,
        "events_retention_hours": settings.EVENTS_RETENTION_HOURS,
        "row_counts": counts,
    }
