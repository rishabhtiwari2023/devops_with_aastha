"""
Node endpoints.

GET /api/nodes                       — list all nodes with latest metrics
GET /api/nodes/compare               — side-by-side comparison of all nodes
GET /api/nodes/summary               — cluster-wide aggregated CPU/RAM/Disk totals (NEW)
GET /api/nodes/{name}                — single node detail + latest metrics
GET /api/nodes/{name}/metrics        — historical NodeMetric time-series
GET /api/nodes/{name}/pods           — pods currently scheduled on this node
GET /api/nodes/{name}/conditions     — node conditions + taints detail (NEW)
GET /api/nodes/{name}/top-pods       — top CPU/memory consuming pods on node (NEW)
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.node import Node
from app.models.pod import Pod
from app.models.metrics import NodeMetric, PodMetric
from app.utils.aggregation import latest_metric_per_node, latest_metric_per_pod, metric_history
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


@router.get("/summary")
def nodes_summary(db: Session = Depends(get_db)):
    """
    NEW: Cluster-wide aggregated CPU/RAM/Disk totals across all nodes,
    using each node's latest NodeMetric snapshot. Powers the "Cluster
    Resources" cards in the dashboard header.

    If Prometheus isn't configured (RCA_PROMETHEUS_URL unset), every node
    will have no NodeMetric rows and this will return zeros with
    `metrics_available: false` — check GET /api/system/health for why.
    """
    nodes = db.query(Node).order_by(Node.name).all()
    latest_by_node = {m.node_name: m for m in latest_metric_per_node(db, NodeMetric)}

    total_cpu_capacity = sum(n.cpu_capacity_millicores or 0 for n in nodes)
    total_mem_capacity = sum(n.mem_capacity_bytes or 0 for n in nodes)

    metrics_present = [latest_by_node[n.name] for n in nodes if n.name in latest_by_node]
    metrics_available = len(metrics_present) > 0

    if metrics_available:
        avg_cpu_pct = sum(m.cpu_pct or 0 for m in metrics_present) / len(metrics_present)
        avg_mem_pct = sum(m.mem_pct or 0 for m in metrics_present) / len(metrics_present)
        avg_disk_pct = sum(m.disk_pct or 0 for m in metrics_present) / len(metrics_present)
        total_mem_used = sum(m.mem_used_bytes or 0 for m in metrics_present)
        total_mem_total = sum(m.mem_total_bytes or 0 for m in metrics_present)
        total_disk_used = sum(m.disk_used_bytes or 0 for m in metrics_present)
        total_disk_total = sum(m.disk_total_bytes or 0 for m in metrics_present)
        nodes_under_memory_pressure = sum(1 for m in metrics_present if m.memory_pressure)
        nodes_under_disk_pressure = sum(1 for m in metrics_present if m.disk_pressure)
    else:
        avg_cpu_pct = avg_mem_pct = avg_disk_pct = 0.0
        total_mem_used = total_mem_total = total_disk_used = total_disk_total = 0.0
        nodes_under_memory_pressure = nodes_under_disk_pressure = 0

    return {
        "metrics_available": metrics_available,
        "total_nodes": len(nodes),
        "ready_nodes": sum(1 for n in nodes if n.status == "Ready"),
        "total_cpu_capacity_millicores": total_cpu_capacity,
        "total_mem_capacity_bytes": total_mem_capacity,
        "avg_cpu_pct": round(avg_cpu_pct, 2),
        "avg_mem_pct": round(avg_mem_pct, 2),
        "avg_disk_pct": round(avg_disk_pct, 2),
        "total_mem_used_bytes": total_mem_used,
        "total_mem_total_bytes": total_mem_total,
        "total_disk_used_bytes": total_disk_used,
        "total_disk_total_bytes": total_disk_total,
        "nodes_under_memory_pressure": nodes_under_memory_pressure,
        "nodes_under_disk_pressure": nodes_under_disk_pressure,
    }


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


@router.get("/{name}/conditions")
def node_conditions(name: str, db: Session = Depends(get_db)):
    """
    NEW: Node conditions (Ready/MemoryPressure/DiskPressure/PIDPressure/
    NetworkUnavailable) and taints in one place, plus the latest pressure
    flags from NodeMetric (which come straight from Prometheus's
    kube_node_status_condition, so they update between full k8s polls).
    """
    node = db.query(Node).filter(Node.name == name).first()
    if not node:
        raise HTTPException(status_code=404, detail=f"Node '{name}' not found")

    latest = latest_metric_per_node(db, NodeMetric)
    m = next((x for x in latest if x.node_name == name), None)

    return {
        "name": node.name,
        "status": node.status,
        "schedulable": node.schedulable,
        "conditions": node.conditions or {},
        "taints": node.taints or [],
        "prometheus_pressure": {
            "memory_pressure": bool(m.memory_pressure) if m else None,
            "disk_pressure": bool(m.disk_pressure) if m else None,
            "pid_pressure": bool(m.pid_pressure) if m else None,
        } if m else None,
    }


@router.get("/{name}/top-pods")
def node_top_pods(
    name: str,
    by: str = Query("cpu", description="cpu or memory"),
    limit: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """
    NEW: Top CPU or memory consuming pods scheduled on this node, using the
    latest PodMetric snapshot. Returns an empty list with a note if
    Prometheus isn't configured (see /api/system/health).
    """
    node = db.query(Node).filter(Node.name == name).first()
    if not node:
        raise HTTPException(status_code=404, detail=f"Node '{name}' not found")

    pods = db.query(Pod).filter(Pod.node_name == name).all()
    pod_uids = {p.uid for p in pods}
    pod_by_uid = {p.uid: p for p in pods}

    latest_metrics = [m for m in latest_metric_per_pod(db, PodMetric) if m.pod_uid in pod_uids]

    sort_key = (lambda m: m.cpu_pct_of_limit or 0) if by == "memory" else (lambda m: m.cpu_pct_of_limit or 0)
    if by == "memory":
        sort_key = lambda m: m.mem_pct_of_limit or 0
    else:
        sort_key = lambda m: m.cpu_pct_of_limit or 0

    ranked = sorted(latest_metrics, key=sort_key, reverse=True)[:limit]

    result = []
    for m in ranked:
        pod = pod_by_uid.get(m.pod_uid)
        d = row_to_dict(m)
        d["pod_name"] = pod.name if pod else m.pod_name
        d["namespace"] = pod.namespace if pod else m.namespace
        d["phase"] = pod.phase if pod else None
        result.append(d)

    return {
        "node": name,
        "sorted_by": by,
        "metrics_available": len(latest_metrics) > 0,
        "pods": result,
    }
