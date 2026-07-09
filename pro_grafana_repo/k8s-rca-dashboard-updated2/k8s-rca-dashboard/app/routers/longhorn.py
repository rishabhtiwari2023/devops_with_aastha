"""
Longhorn storage endpoints.

GET /api/longhorn/volumes                        — list all volumes (latest state)
GET /api/longhorn/volumes/{volume_name}          — single volume detail
GET /api/longhorn/volumes/{volume_name}/history  — IO/rebuild time-series
GET /api/longhorn/volumes/{volume_name}/pods     — pods consuming this volume
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.longhorn import LonghornVolumeMetric
from app.models.pod import Pod
from app.utils.aggregation import metric_history
from app.utils.serializers import row_to_dict, rows_to_list, paginate_list
from sqlalchemy import func

router = APIRouter(prefix="/api/longhorn", tags=["longhorn"])


def _latest_per_volume(db: Session):
    """Return one (latest) LonghornVolumeMetric row per distinct volume_name."""
    subq = (
        db.query(func.max(LonghornVolumeMetric.id).label("max_id"))
        .group_by(LonghornVolumeMetric.volume_name)
        .subquery()
    )
    return (
        db.query(LonghornVolumeMetric)
        .join(subq, LonghornVolumeMetric.id == subq.c.max_id)
        .all()
    )


@router.get("/volumes")
def list_volumes(
    state: str = Query(None, description="Filter by volume state: attached/detached/faulted"),
    robustness: str = Query(None, description="Filter by robustness: healthy/degraded/faulted"),
    rebuilding: bool = Query(None, description="Show only volumes currently rebuilding"),
    page: int = Query(None, ge=1, description="Page number for pagination"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    db: Session = Depends(get_db),
):
    """All Longhorn volumes with their latest collected state snapshot."""
    rows = _latest_per_volume(db)

    if state:
        rows = [r for r in rows if r.state == state]
    if robustness:
        rows = [r for r in rows if r.robustness == robustness]
    if rebuilding is not None:
        rows = [r for r in rows if r.rebuild_in_progress == rebuilding]

    return paginate_list(rows_to_list(rows), page=page, page_size=page_size)


@router.get("/volumes/summary")
def volumes_summary(db: Session = Depends(get_db)):
    """Aggregate counts: healthy, degraded, faulted, rebuilding."""
    rows = _latest_per_volume(db)
    return {
        "total": len(rows),
        "healthy": sum(1 for r in rows if r.robustness == "healthy"),
        "degraded": sum(1 for r in rows if r.robustness == "degraded"),
        "faulted": sum(1 for r in rows if r.robustness == "faulted"),
        "rebuilding": sum(1 for r in rows if r.rebuild_in_progress),
        "attached": sum(1 for r in rows if r.state == "attached"),
        "detached": sum(1 for r in rows if r.state == "detached"),
    }


@router.get("/volumes/{volume_name}")
def volume_detail(volume_name: str, db: Session = Depends(get_db)):
    """Latest state snapshot for a single volume."""
    row = (
        db.query(LonghornVolumeMetric)
        .filter(LonghornVolumeMetric.volume_name == volume_name)
        .order_by(LonghornVolumeMetric.id.desc())
        .first()
    )
    if not row:
        raise HTTPException(
            status_code=404, detail=f"Volume '{volume_name}' not found"
        )
    d = row_to_dict(row)
    # Attach pods that reference this volume
    pods = db.query(Pod).filter(
        Pod.longhorn_volumes.contains(volume_name)  # JSON array contains
    ).all()
    d["pods"] = [
        {
            "uid": p.uid,
            "name": p.name,
            "namespace": p.namespace,
            "phase": p.phase,
            "node_name": p.node_name,
        }
        for p in pods
    ]
    return d


@router.get("/volumes/{volume_name}/history")
def volume_history(
    volume_name: str,
    minutes: int = Query(60, ge=5, le=10080),
    db: Session = Depends(get_db),
):
    """Historical LonghornVolumeMetric rows for IO/rebuild trend charts."""
    # Check volume exists
    exists = (
        db.query(LonghornVolumeMetric)
        .filter(LonghornVolumeMetric.volume_name == volume_name)
        .first()
    )
    if not exists:
        raise HTTPException(
            status_code=404, detail=f"Volume '{volume_name}' not found"
        )
    rows = metric_history(
        db, LonghornVolumeMetric, "volume_name", volume_name, minutes=minutes
    )
    return rows_to_list(rows)


@router.get("/volumes/{volume_name}/pods")
def volume_pods(volume_name: str, db: Session = Depends(get_db)):
    """Pods that reference this Longhorn volume via their PVCs."""
    # Pod.longhorn_volumes is a JSON list; SQLite JSON contains search
    pods = (
        db.query(Pod)
        .filter(Pod.longhorn_volumes.contains(volume_name))
        .all()
    )
    return rows_to_list(pods)
