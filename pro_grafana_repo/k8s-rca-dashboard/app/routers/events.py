"""
Events and Timeline endpoints.

GET /api/events                   — paginated K8s events with filtering
GET /api/events/restarts          — restart history across all pods
GET /api/events/timeline          — unified chronological timeline combining
                                    events, restarts, RCA records and
                                    Longhorn rebuild milestones
"""

from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.events import K8sEvent, RestartHistory
from app.models.rca import RootCauseRecord
from app.models.longhorn import LonghornVolumeMetric
from app.utils.serializers import row_to_dict, rows_to_list

router = APIRouter(prefix="/api/events", tags=["events"])


@router.get("")
def list_events(
    namespace: str = Query(None),
    node: str = Query(None),
    event_type: str = Query(None, description="Normal or Warning"),
    reason: str = Query(None, description="Evicted, OOMKilling, BackOff, …"),
    object_name: str = Query(None, description="Pod/Node name"),
    minutes: int = Query(60, ge=1, le=43200),
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    """
    Recent Kubernetes events.  Defaults to the last 60 minutes.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    q = db.query(K8sEvent).filter(K8sEvent.last_seen >= cutoff)

    if namespace:
        q = q.filter(K8sEvent.namespace == namespace)
    if node:
        q = q.filter(K8sEvent.node_name == node)
    if event_type:
        q = q.filter(K8sEvent.event_type == event_type)
    if reason:
        q = q.filter(K8sEvent.reason == reason)
    if object_name:
        q = q.filter(K8sEvent.involved_object_name.contains(object_name))

    rows = q.order_by(K8sEvent.last_seen.desc()).limit(limit).all()
    return rows_to_list(rows)


@router.get("/restarts")
def list_restarts(
    namespace: str = Query(None),
    node: str = Query(None),
    pod_name: str = Query(None),
    minutes: int = Query(60, ge=1, le=43200),
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    """
    Container restart events, newest first.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    q = db.query(RestartHistory).filter(RestartHistory.timestamp >= cutoff)

    if namespace:
        q = q.filter(RestartHistory.namespace == namespace)
    if node:
        q = q.filter(RestartHistory.node_name == node)
    if pod_name:
        q = q.filter(RestartHistory.pod_name.contains(pod_name))

    rows = q.order_by(RestartHistory.timestamp.desc()).limit(limit).all()
    return rows_to_list(rows)


@router.get("/timeline")
def timeline(
    minutes: int = Query(60, ge=5, le=1440),
    namespace: str = Query(None),
    node: str = Query(None),
    pod_name: str = Query(None),
    db: Session = Depends(get_db),
):
    """
    Unified chronological timeline.

    Merges K8s events, restart records, RCA verdicts and Longhorn rebuild
    state changes into a single list sorted by timestamp descending.
    Each item has:
        {
          "timestamp": <ISO>,
          "kind": "event" | "restart" | "rca" | "longhorn",
          "severity": "critical" | "warning" | "info" | "normal",
          "title": <short label>,
          "detail": <longer description>,
          "pod_name": <str or null>,
          "namespace": <str or null>,
          "node_name": <str or null>
        }
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    items: list[dict] = []

    # ---- K8s Warning events ------------------------------------------------
    k8s_q = db.query(K8sEvent).filter(
        K8sEvent.last_seen >= cutoff,
        K8sEvent.event_type == "Warning",
    )
    if namespace:
        k8s_q = k8s_q.filter(K8sEvent.namespace == namespace)
    if node:
        k8s_q = k8s_q.filter(K8sEvent.node_name == node)
    if pod_name:
        k8s_q = k8s_q.filter(K8sEvent.involved_object_name.contains(pod_name))

    for ev in k8s_q.all():
        items.append(
            {
                "timestamp": ev.last_seen.isoformat() if ev.last_seen else None,
                "kind": "event",
                "severity": "warning",
                "title": ev.reason,
                "detail": ev.message,
                "pod_name": ev.involved_object_name,
                "namespace": ev.namespace,
                "node_name": ev.node_name or None,
            }
        )

    # ---- Restart history ---------------------------------------------------
    rst_q = db.query(RestartHistory).filter(RestartHistory.timestamp >= cutoff)
    if namespace:
        rst_q = rst_q.filter(RestartHistory.namespace == namespace)
    if node:
        rst_q = rst_q.filter(RestartHistory.node_name == node)
    if pod_name:
        rst_q = rst_q.filter(RestartHistory.pod_name.contains(pod_name))

    for rst in rst_q.all():
        severity = "critical" if rst.last_state_reason == "OOMKilled" else "warning"
        items.append(
            {
                "timestamp": rst.timestamp.isoformat() if rst.timestamp else None,
                "kind": "restart",
                "severity": severity,
                "title": f"Container restarted ({rst.last_state_reason or 'unknown reason'})",
                "detail": (
                    f"{rst.pod_name}/{rst.container_name} — "
                    f"count: {rst.previous_restart_count} → {rst.restart_count}, "
                    f"exit_code: {rst.exit_code}"
                ),
                "pod_name": rst.pod_name,
                "namespace": rst.namespace,
                "node_name": rst.node_name,
            }
        )

    # ---- RCA verdicts -------------------------------------------------------
    rca_q = db.query(RootCauseRecord).filter(RootCauseRecord.timestamp >= cutoff)
    if namespace:
        rca_q = rca_q.filter(RootCauseRecord.namespace == namespace)
    if node:
        rca_q = rca_q.filter(RootCauseRecord.node_name == node)
    if pod_name:
        rca_q = rca_q.filter(RootCauseRecord.pod_name.contains(pod_name))

    for rca in rca_q.all():
        items.append(
            {
                "timestamp": rca.timestamp.isoformat() if rca.timestamp else None,
                "kind": "rca",
                "severity": rca.severity,
                "title": rca.short_reason,
                "detail": rca.explanation,
                "pod_name": rca.pod_name,
                "namespace": rca.namespace,
                "node_name": rca.node_name,
                "reason_code": rca.reason_code,
                "category": rca.category,
            }
        )

    # ---- Longhorn rebuild state changes ------------------------------------
    lh_q = db.query(LonghornVolumeMetric).filter(
        LonghornVolumeMetric.timestamp >= cutoff,
        LonghornVolumeMetric.rebuild_in_progress == True,  # noqa: E712
    )
    for lh in lh_q.all():
        items.append(
            {
                "timestamp": lh.timestamp.isoformat() if lh.timestamp else None,
                "kind": "longhorn",
                "severity": "warning" if lh.robustness == "degraded" else "critical",
                "title": f"Longhorn replica rebuilding — {lh.volume_name}",
                "detail": (
                    f"Volume: {lh.volume_name}, PVC: {lh.pvc_name}, "
                    f"attached node: {lh.attached_node}, "
                    f"robustness: {lh.robustness}"
                ),
                "pod_name": None,
                "namespace": lh.namespace or None,
                "node_name": lh.attached_node or None,
                "volume_name": lh.volume_name,
            }
        )

    # Sort all items by timestamp descending (latest first)
    items.sort(
        key=lambda x: x["timestamp"] or "",
        reverse=True,
    )
    return items
