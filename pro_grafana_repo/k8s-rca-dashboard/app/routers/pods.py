"""
Pod endpoints.

GET /api/pods                    — list all pods (filterable)
GET /api/pods/{uid}              — single pod detail + latest metrics
GET /api/pods/{uid}/metrics      — Prometheus CPU/Memory time-series
GET /api/pods/{uid}/docker       — Docker network/IO time-series
GET /api/pods/{uid}/restarts     — restart history for this pod
GET /api/pods/{uid}/events       — recent K8s events for this pod
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.pod import Pod
from app.models.metrics import PodMetric, DockerMetric
from app.models.events import K8sEvent, RestartHistory
from app.utils.aggregation import latest_metric_per_pod, metric_history
from app.utils.serializers import row_to_dict, rows_to_list

router = APIRouter(prefix="/api/pods", tags=["pods"])


@router.get("")
def list_pods(
    namespace: str = Query(None),
    node: str = Query(None),
    phase: str = Query(None),
    owner: str = Query(None),
    search: str = Query(None),
    db: Session = Depends(get_db),
):
    """
    List all pods.  Optional filters:
    - `namespace` — exact namespace name
    - `node`      — node name
    - `phase`     — Running / Pending / Failed / Succeeded / Unknown
    - `owner`     — deployment / statefulset / daemonset name
    - `search`    — substring match on pod name
    """
    q = db.query(Pod)
    if namespace:
        q = q.filter(Pod.namespace == namespace)
    if node:
        q = q.filter(Pod.node_name == node)
    if phase:
        q = q.filter(Pod.phase == phase)
    if owner:
        q = q.filter(
            (Pod.deployment == owner)
            | (Pod.statefulset == owner)
            | (Pod.daemonset == owner)
            | (Pod.owner_name == owner)
        )
    if search:
        q = q.filter(Pod.name.contains(search))

    pods = q.order_by(Pod.namespace, Pod.name).all()

    # Attach latest metric snapshots so the list view renders resource columns
    latest_prom = {m.pod_uid: m for m in latest_metric_per_pod(db, PodMetric)}
    latest_docker = {m.pod_uid: m for m in latest_metric_per_pod(db, DockerMetric)}

    result = []
    for pod in pods:
        d = pod.to_dict()
        pm = latest_prom.get(pod.uid)
        dm = latest_docker.get(pod.uid)
        d["cpu_metrics"] = row_to_dict(pm) if pm else {}
        d["docker_metrics"] = row_to_dict(dm) if dm else {}
        result.append(d)
    return result


@router.get("/{uid}")
def pod_detail(uid: str, db: Session = Depends(get_db)):
    """Full pod record: containers, latest metrics, and 20 most-recent events."""
    pod = db.query(Pod).filter(Pod.uid == uid).first()
    if not pod:
        raise HTTPException(status_code=404, detail=f"Pod '{uid}' not found")

    d = pod.to_dict()

    pm = (
        db.query(PodMetric)
        .filter(PodMetric.pod_uid == uid)
        .order_by(PodMetric.id.desc())
        .first()
    )
    dm = (
        db.query(DockerMetric)
        .filter(DockerMetric.pod_uid == uid)
        .order_by(DockerMetric.id.desc())
        .first()
    )
    d["cpu_metrics"] = row_to_dict(pm) if pm else {}
    d["docker_metrics"] = row_to_dict(dm) if dm else {}

    recent_events = (
        db.query(K8sEvent)
        .filter(
            K8sEvent.involved_object_name == pod.name,
            K8sEvent.namespace == pod.namespace,
        )
        .order_by(K8sEvent.last_seen.desc())
        .limit(20)
        .all()
    )
    d["recent_events"] = rows_to_list(recent_events)
    return d


@router.get("/{uid}/metrics")
def pod_metrics_history(
    uid: str,
    minutes: int = Query(60, ge=5, le=10080),
    db: Session = Depends(get_db),
):
    """Prometheus CPU/Memory time-series for chart rendering."""
    _require_pod(uid, db)
    rows = metric_history(db, PodMetric, "pod_uid", uid, minutes=minutes)
    return rows_to_list(rows)


@router.get("/{uid}/docker")
def pod_docker_history(
    uid: str,
    minutes: int = Query(60, ge=5, le=10080),
    db: Session = Depends(get_db),
):
    """Docker network/IO time-series for chart rendering."""
    _require_pod(uid, db)
    rows = metric_history(db, DockerMetric, "pod_uid", uid, minutes=minutes)
    return rows_to_list(rows)


@router.get("/{uid}/restarts")
def pod_restart_history(
    uid: str,
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """Restart history rows for this pod, newest first."""
    _require_pod(uid, db)
    rows = (
        db.query(RestartHistory)
        .filter(RestartHistory.pod_uid == uid)
        .order_by(RestartHistory.timestamp.desc())
        .limit(limit)
        .all()
    )
    return rows_to_list(rows)


@router.get("/{uid}/events")
def pod_events(
    uid: str,
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """Recent K8s events involving this pod, newest first."""
    pod = _require_pod(uid, db)
    rows = (
        db.query(K8sEvent)
        .filter(
            K8sEvent.involved_object_name == pod.name,
            K8sEvent.namespace == pod.namespace,
        )
        .order_by(K8sEvent.last_seen.desc())
        .limit(limit)
        .all()
    )
    return rows_to_list(rows)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_pod(uid: str, db: Session) -> Pod:
    pod = db.query(Pod).filter(Pod.uid == uid).first()
    if not pod:
        raise HTTPException(status_code=404, detail=f"Pod '{uid}' not found")
    return pod
