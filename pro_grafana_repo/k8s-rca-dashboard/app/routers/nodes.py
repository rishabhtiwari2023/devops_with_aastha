"""
Node endpoints.

GET /api/nodes                       — list all nodes with latest metrics
GET /api/nodes/compare               — side-by-side comparison of all nodes
GET /api/nodes/{name}                — single node detail + latest metrics
GET /api/nodes/{name}/metrics        — historical NodeMetric time-series
GET /api/nodes/{name}/pods           — pods currently scheduled on this node
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.node import Node
from app.models.pod import Pod
from app.models.metrics import NodeMetric
from app.utils.aggregation import latest_metric_per_node, metric_history
from app.utils.serializers import row_to_dict, rows_to_list

router = APIRouter(prefix="/api/nodes", tags=["nodes"])


@router.get("")
def list_nodes(db: Session = Depends(get_db)):
    """All nodes with the latest collected NodeMetric snapshot attached."""
    nodes = db.query(Node).order_by(Node.name).all()
    latest_by_node = {m.node_name: m for m in latest_metric_per_node(db, NodeMetric)}

    result = []
    for node in nodes:
        d = node.to_dict()
        m = latest_by_node.get(node.name)
        d["metrics"] = row_to_dict(m) if m else {}
        result.append(d)
    return result


@router.get("/compare")
def nodes_compare(db: Session = Depends(get_db)):
    """
    Return all nodes side-by-side with their latest metrics.
    Used by the Node Comparison panel in the dashboard.
    """
    nodes = db.query(Node).order_by(Node.name).all()
    latest_by_node = {m.node_name: m for m in latest_metric_per_node(db, NodeMetric)}
    pod_counts = {
        row[0]: row[1]
        for row in db.query(Pod.node_name, Pod.node_name)
        .group_by(Pod.node_name)
        .all()
    }
    # Re-count pods properly
    from sqlalchemy import func as sqlfunc
    pod_count_rows = (
        db.query(Pod.node_name, sqlfunc.count(Pod.uid).label("cnt"))
        .group_by(Pod.node_name)
        .all()
    )
    pod_counts = {r.node_name: r.cnt for r in pod_count_rows}

    result = []
    for node in nodes:
        d = node.to_dict()
        m = latest_by_node.get(node.name)
        d["metrics"] = row_to_dict(m) if m else {}
        d["pod_count_live"] = pod_counts.get(node.name, 0)
        result.append(d)
    return result


@router.get("/{name}")
def node_detail(name: str, db: Session = Depends(get_db)):
    """Single node with its latest metric snapshot and live pod count."""
    node = db.query(Node).filter(Node.name == name).first()
    if not node:
        raise HTTPException(status_code=404, detail=f"Node '{name}' not found")

    d = node.to_dict()
    latest = latest_metric_per_node(db, NodeMetric)
    m = next((x for x in latest if x.node_name == name), None)
    d["metrics"] = row_to_dict(m) if m else {}
    d["pod_count_live"] = db.query(Pod).filter(Pod.node_name == name).count()
    return d


@router.get("/{name}/metrics")
def node_metrics_history(
    name: str,
    minutes: int = Query(60, ge=5, le=10080),
    db: Session = Depends(get_db),
):
    """Historical NodeMetric rows for sparklines and trend charts."""
    node = db.query(Node).filter(Node.name == name).first()
    if not node:
        raise HTTPException(status_code=404, detail=f"Node '{name}' not found")

    rows = metric_history(db, NodeMetric, "node_name", name, minutes=minutes)
    return rows_to_list(rows)


@router.get("/{name}/pods")
def node_pods(
    name: str,
    namespace: str = Query(None),
    db: Session = Depends(get_db),
):
    """All pods currently scheduled on a given node, optionally filtered by namespace."""
    node = db.query(Node).filter(Node.name == name).first()
    if not node:
        raise HTTPException(status_code=404, detail=f"Node '{name}' not found")

    q = db.query(Pod).filter(Pod.node_name == name)
    if namespace:
        q = q.filter(Pod.namespace == namespace)
    pods = q.order_by(Pod.namespace, Pod.name).all()
    return rows_to_list(pods)
