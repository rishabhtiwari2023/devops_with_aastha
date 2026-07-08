"""
Alert endpoints.

GET  /api/alerts               — list active (unresolved) alerts
GET  /api/alerts/history       — all alerts including resolved
GET  /api/alerts/counts        — severity counts (for badge/header)
GET  /api/alerts/{id}          — single alert detail
POST /api/alerts/{id}/ack      — acknowledge an alert
POST /api/alerts/{id}/resolve  — mark an alert as resolved
"""

from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.core.database import get_db
from app.models.alerts import Alert
from app.utils.serializers import row_to_dict, rows_to_list

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


@router.get("")
def list_active_alerts(
    severity: str = Query(None, description="critical/warning/healthy"),
    node: str = Query(None),
    namespace: str = Query(None),
    source: str = Query(None, description="rca/k8s/docker/longhorn/prometheus"),
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    """
    Active (unresolved) alerts, newest first.
    """
    q = db.query(Alert).filter(Alert.resolved == False)  # noqa: E712

    if severity:
        q = q.filter(Alert.severity == severity)
    if node:
        q = q.filter(Alert.node_name == node)
    if namespace:
        q = q.filter(Alert.namespace == namespace)
    if source:
        q = q.filter(Alert.source == source)

    rows = q.order_by(Alert.timestamp.desc()).limit(limit).all()
    return rows_to_list(rows)


@router.get("/history")
def alert_history(
    severity: str = Query(None),
    node: str = Query(None),
    namespace: str = Query(None),
    minutes: int = Query(1440, ge=5, le=43200),
    limit: int = Query(500, ge=1, le=2000),
    db: Session = Depends(get_db),
):
    """
    Historical alerts (all, including resolved), newest first.
    Defaults to last 24 hours.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    q = db.query(Alert).filter(Alert.timestamp >= cutoff)

    if severity:
        q = q.filter(Alert.severity == severity)
    if node:
        q = q.filter(Alert.node_name == node)
    if namespace:
        q = q.filter(Alert.namespace == namespace)

    rows = q.order_by(Alert.timestamp.desc()).limit(limit).all()
    return rows_to_list(rows)


@router.get("/counts")
def alert_counts(db: Session = Depends(get_db)):
    """
    Current unresolved alert counts by severity.
    Used by the dashboard header badges.
    """
    rows = (
        db.query(Alert.severity, func.count(Alert.id).label("count"))
        .filter(Alert.resolved == False)  # noqa: E712
        .group_by(Alert.severity)
        .all()
    )
    counts = {r.severity: r.count for r in rows}
    return {
        "critical": counts.get("critical", 0),
        "warning": counts.get("warning", 0),
        "healthy": counts.get("healthy", 0),
        "total": sum(counts.values()),
    }


@router.get("/{alert_id}")
def alert_detail(alert_id: int, db: Session = Depends(get_db)):
    """Full details for a single alert including linked RCA id."""
    alert = db.query(Alert).filter(Alert.id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail=f"Alert {alert_id} not found")
    return row_to_dict(alert)


@router.post("/{alert_id}/ack")
def acknowledge_alert(alert_id: int, db: Session = Depends(get_db)):
    """
    Acknowledge an alert (sets acknowledged=True).
    Does not resolve it — the pod may still be unhealthy.
    """
    alert = _require_alert(alert_id, db)
    alert.acknowledged = True
    db.commit()
    return {"status": "acknowledged", "id": alert_id}


@router.post("/{alert_id}/resolve")
def resolve_alert(alert_id: int, db: Session = Depends(get_db)):
    """
    Manually mark an alert as resolved.
    The RCA engine may re-raise it if the condition persists.
    """
    alert = _require_alert(alert_id, db)
    alert.resolved = True
    alert.resolved_at = datetime.now(timezone.utc)
    db.commit()
    return {"status": "resolved", "id": alert_id}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_alert(alert_id: int, db: Session) -> Alert:
    alert = db.query(Alert).filter(Alert.id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail=f"Alert {alert_id} not found")
    return alert
