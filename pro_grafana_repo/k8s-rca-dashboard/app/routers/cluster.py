"""
Cluster-level summary and tree-view endpoints.

GET /api/cluster/summary  — headline KPIs shown in the dashboard banner
GET /api/cluster/tree     — full hierarchy cluster→node→namespace→owner→pod
"""

from collections import defaultdict
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.node import Node
from app.models.pod import Pod
from app.models.alerts import Alert
from app.models.events import RestartHistory

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
