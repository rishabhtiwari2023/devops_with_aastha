"""
Root Cause Analysis endpoints.

GET /api/rca                    — paginated list of RCA records
GET /api/rca/recent             — latest record per pod (dashboard panel)
GET /api/rca/{id}               — single RCA record detail
GET /api/rca/pod/{uid}          — all RCA records for a specific pod
"""

from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.core.database import get_db
from app.models.rca import RootCauseRecord
from app.utils.serializers import row_to_dict, rows_to_list

router = APIRouter(prefix="/api/rca", tags=["rca"])


@router.get("")
def list_rca(
    namespace: str = Query(None),
    node: str = Query(None),
    category: str = Query(None, description="cpu/memory/disk/network/longhorn/scheduling"),
    severity: str = Query(None, description="critical/warning/info"),
    reason_code: str = Query(None),
    minutes: int = Query(1440, ge=5, le=43200),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    """
    Paginated list of RCA records, newest first.
    Defaults to the last 24 hours.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    q = db.query(RootCauseRecord).filter(RootCauseRecord.timestamp >= cutoff)

    if namespace:
        q = q.filter(RootCauseRecord.namespace == namespace)
    if node:
        q = q.filter(RootCauseRecord.node_name == node)
    if category:
        q = q.filter(RootCauseRecord.category == category)
    if severity:
        q = q.filter(RootCauseRecord.severity == severity)
    if reason_code:
        q = q.filter(RootCauseRecord.reason_code == reason_code)

    rows = q.order_by(RootCauseRecord.timestamp.desc()).limit(limit).all()
    return rows_to_list(rows)


@router.get("/recent")
def rca_recent(
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """
    Latest RCA record per pod (for the RCA summary panel).
    Useful to show "current root cause" next to each pod row.
    """
    subq = (
        db.query(func.max(RootCauseRecord.id).label("max_id"))
        .group_by(RootCauseRecord.pod_uid)
        .subquery()
    )
    rows = (
        db.query(RootCauseRecord)
        .join(subq, RootCauseRecord.id == subq.c.max_id)
        .order_by(RootCauseRecord.timestamp.desc())
        .limit(limit)
        .all()
    )
    return rows_to_list(rows)


@router.get("/summary")
def rca_summary(
    minutes: int = Query(1440, ge=5, le=43200),
    db: Session = Depends(get_db),
):
    """
    Breakdown of RCA incidents by category and severity within `minutes`.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    rows = (
        db.query(
            RootCauseRecord.category,
            RootCauseRecord.severity,
            func.count(RootCauseRecord.id).label("count"),
        )
        .filter(RootCauseRecord.timestamp >= cutoff)
        .group_by(RootCauseRecord.category, RootCauseRecord.severity)
        .all()
    )
    result = [
        {"category": r.category, "severity": r.severity, "count": r.count}
        for r in rows
    ]
    return {
        "period_minutes": minutes,
        "total": sum(r["count"] for r in result),
        "breakdown": result,
    }


@router.get("/pod/{uid}")
def rca_for_pod(
    uid: str,
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """All RCA records for a single pod, newest first."""
    rows = (
        db.query(RootCauseRecord)
        .filter(RootCauseRecord.pod_uid == uid)
        .order_by(RootCauseRecord.timestamp.desc())
        .limit(limit)
        .all()
    )
    return rows_to_list(rows)


@router.get("/{record_id}")
def rca_detail(record_id: int, db: Session = Depends(get_db)):
    """Full detail for a single RCA record, including the raw evidence dict."""
    row = db.query(RootCauseRecord).filter(RootCauseRecord.id == record_id).first()
    if not row:
        raise HTTPException(
            status_code=404, detail=f"RCA record {record_id} not found"
        )
    return row_to_dict(row)
