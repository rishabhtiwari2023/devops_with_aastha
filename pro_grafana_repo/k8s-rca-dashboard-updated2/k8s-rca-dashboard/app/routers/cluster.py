"""
Cluster-level summary and tree-view endpoints.

GET /api/cluster/summary    — headline KPIs shown in the dashboard banner
GET /api/cluster/tree       — full hierarchy cluster→node→namespace→owner→pod
GET /api/cluster/resources  — cluster-wide CPU/RAM/Disk aggregate (NEW)
GET /api/cluster/failures   — pods currently failing/crashing WITH their RCA
                              reason attached in the same response (NEW) —
                              this is the "why did my pod fail?" endpoint.
"""

from collections import defaultdict
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.core.database import get_db
from app.models.node import Node
from app.models.pod import Pod
from app.models.metrics import NodeMetric
from app.models.alerts import Alert
from app.models.events import RestartHistory
from app.models.rca import RootCauseRecord
from app.utils.aggregation import latest_metric_per_node
from app.utils.serializers import row_to_dict

router = APIRouter(prefix="/api/cluster", tags=["cluster"])


@router.get("/summary")
def cluster_summary(db: Session = Depends(get_db)):
    """
    Headline KPIs: node counts, pod phase breakdown, alert counts,
    recent restart count, and an overall cluster health label.
    """
    nodes = db.query(Node).all()
    pods = db.query(Pod).all()

    total_nodes = len(nodes)
    healthy_nodes = sum(1 for n in nodes if n.status == "Ready")

    total_pods = len(pods)
    running_pods = sum(1 for p in pods if p.phase == "Running" and p.ready)
    pending_pods = sum(1 for p in pods if p.phase == "Pending")
    failed_pods = sum(1 for p in pods if p.phase in ("Failed", "Unknown"))
    not_ready_pods = sum(
        1 for p in pods if p.phase == "Running" and not p.ready
    )

    cutoff_1h = datetime.now(timezone.utc) - timedelta(hours=1)
    critical_alerts = (
        db.query(Alert)
        .filter(
            Alert.severity == "critical",
            Alert.resolved == False,  # noqa: E712
            Alert.timestamp >= cutoff_1h,
        )
        .count()
    )
    warning_alerts = (
        db.query(Alert)
        .filter(
            Alert.severity == "warning",
            Alert.resolved == False,  # noqa: E712
            Alert.timestamp >= cutoff_1h,
        )
        .count()
    )
    recent_restarts = (
        db.query(RestartHistory)
        .filter(RestartHistory.timestamp >= cutoff_1h)
        .count()
    )

    namespaces = len({p.namespace for p in pods if p.namespace})

    if critical_alerts > 0:
        health = "critical"
    elif warning_alerts > 0:
        health = "warning"
    else:
        health = "healthy"

    return {
        "total_nodes": total_nodes,
        "healthy_nodes": healthy_nodes,
        "unhealthy_nodes": total_nodes - healthy_nodes,
        "total_pods": total_pods,
        "running_pods": running_pods,
        "pending_pods": pending_pods,
        "failed_pods": failed_pods,
        "not_ready_pods": not_ready_pods,
        "namespaces": namespaces,
        "critical_alerts": critical_alerts,
        "warning_alerts": warning_alerts,
        "recent_restarts_1h": recent_restarts,
        "cluster_health": health,
    }


@router.get("/tree")
def cluster_tree(db: Session = Depends(get_db)):
    """
    Hierarchical tree view used by the dashboard's left-panel component.

    Returns::

        {
          "name": "cluster",
          "nodes": [
            {
              "name": "server-1", "status": "Ready", "pod_count": 12,
              "namespaces": [
                {
                  "name": "monitoring",
                  "deployments": [
                    {
                      "name": "grafana", "kind": "Deployment",
                      "pods": [
                        {"uid": "...", "name": "grafana-xxx", "phase": "Running",
                         "ready": true, "restart_count": 0, ...}
                      ]
                    }
                  ]
                }
              ]
            }
          ]
        }
    """
    node_objs = {n.name: n for n in db.query(Node).all()}
    pods = db.query(Pod).all()

    # Build: node_name → namespace → (owner_kind, owner_name) → [pods]
    tree: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for pod in pods:
        node_key = pod.node_name or "_unscheduled"
        ns_key = pod.namespace or "_unknown"
        owner_key = (
            pod.owner_kind or "Pod",
            pod.owner_name or pod.name or pod.uid,
        )
        tree[node_key][ns_key][owner_key].append(pod)

    result_nodes = []
    for node_name, ns_map in tree.items():
        node_obj = node_objs.get(node_name)
        pod_count = sum(
            len(plist)
            for owner_map in ns_map.values()
            for plist in owner_map.values()
        )
        namespaces = []
        for ns_name, owner_map in sorted(ns_map.items()):
            deployments = []
            for (kind, owner_name), pod_list in sorted(
                owner_map.items(), key=lambda x: x[0][1]
            ):
                deployments.append(
                    {
                        "name": owner_name,
                        "kind": kind,
                        "pod_count": len(pod_list),
                        "pods": [
                            {
                                "uid": p.uid,
                                "name": p.name,
                                "phase": p.phase,
                                "ready": p.ready,
                                "restart_count": p.restart_count,
                                "node_name": p.node_name,
                                "status_reason": p.status_reason,
                            }
                            for p in sorted(pod_list, key=lambda x: x.name)
                        ],
                    }
                )
            namespaces.append({"name": ns_name, "deployments": deployments})

        result_nodes.append(
            {
                "name": node_name,
                "status": node_obj.status if node_obj else "Unknown",
                "roles": node_obj.roles if node_obj else "",
                "pod_count": pod_count,
                "namespaces": namespaces,
            }
        )

    # _unscheduled last, then alphabetical
    result_nodes.sort(key=lambda n: (n["name"] == "_unscheduled", n["name"]))
    return {"name": "cluster", "nodes": result_nodes}


@router.get("/resources")
def cluster_resources(db: Session = Depends(get_db)):
    """
    NEW: Cluster-wide CPU / RAM / Disk aggregate, for the "Cluster Resources"
    cards at the top of the dashboard. Uses each node's latest NodeMetric
    row (sourced from Prometheus). If those are all missing because
    RCA_PROMETHEUS_URL isn't configured, `metrics_available` is false and
    the UI should point the user at GET /api/system/health for the reason.
    """
    nodes = db.query(Node).all()
    latest = latest_metric_per_node(db, NodeMetric)
    metrics_available = len(latest) > 0

    if metrics_available:
        avg_cpu_pct = sum(m.cpu_pct or 0 for m in latest) / len(latest)
        total_mem_used = sum(m.mem_used_bytes or 0 for m in latest)
        total_mem_total = sum(m.mem_total_bytes or 0 for m in latest)
        total_disk_used = sum(m.disk_used_bytes or 0 for m in latest)
        total_disk_total = sum(m.disk_total_bytes or 0 for m in latest)
        avg_mem_pct = (total_mem_used / total_mem_total * 100.0) if total_mem_total else 0.0
        avg_disk_pct = (total_disk_used / total_disk_total * 100.0) if total_disk_total else 0.0
        per_node = [
            {
                "node_name": m.node_name,
                "cpu_pct": m.cpu_pct,
                "mem_pct": m.mem_pct,
                "disk_pct": m.disk_pct,
                "load1": m.load1,
                "memory_pressure": bool(m.memory_pressure),
                "disk_pressure": bool(m.disk_pressure),
            }
            for m in sorted(latest, key=lambda x: x.node_name)
        ]
    else:
        avg_cpu_pct = avg_mem_pct = avg_disk_pct = 0.0
        total_mem_used = total_mem_total = total_disk_used = total_disk_total = 0.0
        per_node = []

    return {
        "metrics_available": metrics_available,
        "total_nodes": len(nodes),
        "cluster_avg_cpu_pct": round(avg_cpu_pct, 2),
        "cluster_avg_mem_pct": round(avg_mem_pct, 2),
        "cluster_avg_disk_pct": round(avg_disk_pct, 2),
        "total_mem_used_bytes": total_mem_used,
        "total_mem_total_bytes": total_mem_total,
        "total_disk_used_bytes": total_disk_used,
        "total_disk_total_bytes": total_disk_total,
        "per_node": per_node,
    }


@router.get("/failures")
def cluster_failures(
    minutes: int = Query(1440, ge=5, le=43200),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """
    NEW: The "why did my pod fail?" feed. Returns every pod currently in a
    Failed/Unknown phase or not-ready-Running, plus (if one exists) the
    most recent RCA record explaining *why* — category, severity, short
    human-readable reason, and the full explanation sentence — attached
    directly in the same response, joined with recently-restarted pods
    within the lookback window so transient CrashLoopBackOff pods that have
    since recovered still show up with their explanation.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)

    # Pods currently unhealthy right now
    unhealthy_pods = (
        db.query(Pod)
        .filter(
            (Pod.phase.in_(["Failed", "Unknown"]))
            | ((Pod.phase == "Running") & (Pod.ready == False))  # noqa: E712
        )
        .all()
    )
    unhealthy_uids = {p.uid for p in unhealthy_pods}

    # Pods that had a restart recently (may already be back to Running)
    recent_restart_uids = {
        r.pod_uid
        for r in db.query(RestartHistory.pod_uid)
        .filter(RestartHistory.timestamp >= cutoff)
        .distinct()
        .all()
    }

    all_uids = unhealthy_uids | recent_restart_uids
    if not all_uids:
        return []

    pods_by_uid = {p.uid: p for p in db.query(Pod).filter(Pod.uid.in_(all_uids)).all()}

    # Latest RCA record per pod uid (only for the uids we care about)
    subq = (
        db.query(func.max(RootCauseRecord.id).label("max_id"))
        .filter(RootCauseRecord.pod_uid.in_(all_uids))
        .group_by(RootCauseRecord.pod_uid)
        .subquery()
    )
    rca_rows = (
        db.query(RootCauseRecord)
        .join(subq, RootCauseRecord.id == subq.c.max_id)
        .all()
    )
    rca_by_uid = {r.pod_uid: r for r in rca_rows}

    result = []
    for uid in all_uids:
        pod = pods_by_uid.get(uid)
        rca = rca_by_uid.get(uid)
        result.append({
            "pod_uid": uid,
            "pod_name": pod.name if pod else None,
            "namespace": pod.namespace if pod else None,
            "node_name": pod.node_name if pod else None,
            "phase": pod.phase if pod else None,
            "ready": pod.ready if pod else None,
            "restart_count": pod.restart_count if pod else None,
            "status_reason": pod.status_reason if pod else None,
            "root_cause": row_to_dict(rca) if rca else None,
            "reason_available": rca is not None,
        })

    # Most recent RCA timestamp first; pods with no RCA yet go last.
    result.sort(
        key=lambda r: (r["root_cause"] or {}).get("timestamp", ""),
        reverse=True,
    )
    return result[:limit]
