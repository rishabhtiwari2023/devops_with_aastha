"""
Resource Rankings endpoints.

Returns ALL pods sorted descending by each resource type.
The brief explicitly says "Do NOT show only Top 3 — show ALL pods."

GET /api/rankings/cpu        — all pods sorted by CPU % of limit desc
GET /api/rankings/memory     — all pods sorted by Memory % of limit desc
GET /api/rankings/network    — all pods sorted by max(net_rx, net_tx) desc
GET /api/rankings/disk       — all pods sorted by blk_read+write per sec desc
GET /api/rankings/iops       — all pods sorted by IOPS desc
GET /api/rankings/restarts   — all pods sorted by total restart count desc
GET /api/rankings/all        — combined payload with all six tables
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.core.database import get_db
from app.models.pod import Pod
from app.models.metrics import PodMetric, DockerMetric
from app.utils.aggregation import latest_metric_per_pod
from app.utils.serializers import row_to_dict

router = APIRouter(prefix="/api/rankings", tags=["rankings"])


def _pod_lookup(db: Session) -> dict[str, dict]:
    """Build a uid→{name, namespace, node_name, phase} map once per request."""
    return {
        p.uid: {
            "uid": p.uid,
            "name": p.name,
            "namespace": p.namespace,
            "node_name": p.node_name,
            "phase": p.phase,
            "ready": p.ready,
        }
        for p in db.query(
            Pod.uid, Pod.name, Pod.namespace, Pod.node_name, Pod.phase, Pod.ready
        ).all()
    }


def _merge(pod_map: dict, uid: str, extra: dict) -> dict:
    base = pod_map.get(
        uid,
        {"uid": uid, "name": uid, "namespace": "", "node_name": "", "phase": ""},
    )
    return {**base, **extra}


@router.get("/cpu")
def rank_cpu(db: Session = Depends(get_db)):
    """All pods ranked by CPU % of limit (highest first)."""
    pod_map = _pod_lookup(db)
    rows = latest_metric_per_pod(db, PodMetric)
    ranked = sorted(rows, key=lambda r: r.cpu_pct_of_limit, reverse=True)
    return [
        _merge(
            pod_map,
            r.pod_uid,
            {
                "cpu_pct_of_limit": round(r.cpu_pct_of_limit, 2),
                "cpu_usage_millicores": round(r.cpu_usage_millicores, 1),
                "cpu_limit_millicores": round(r.cpu_limit_millicores, 1),
            },
        )
        for r in ranked
    ]


@router.get("/memory")
def rank_memory(db: Session = Depends(get_db)):
    """All pods ranked by Memory % of limit (highest first)."""
    pod_map = _pod_lookup(db)
    rows = latest_metric_per_pod(db, PodMetric)
    ranked = sorted(rows, key=lambda r: r.mem_pct_of_limit, reverse=True)
    return [
        _merge(
            pod_map,
            r.pod_uid,
            {
                "mem_pct_of_limit": round(r.mem_pct_of_limit, 2),
                "mem_usage_bytes": r.mem_usage_bytes,
                "mem_limit_bytes": r.mem_limit_bytes,
                "mem_usage_mb": round(r.mem_usage_bytes / 1_048_576, 1),
            },
        )
        for r in ranked
    ]


@router.get("/network")
def rank_network(db: Session = Depends(get_db)):
    """All pods ranked by peak network throughput (max of RX/TX bytes/sec)."""
    pod_map = _pod_lookup(db)
    rows = latest_metric_per_pod(db, DockerMetric)

    # Aggregate per pod_uid: sum container rows belonging to the same pod
    by_pod: dict[str, dict] = {}
    for r in rows:
        uid = r.pod_uid
        if uid not in by_pod:
            by_pod[uid] = {
                "pod_uid": uid,
                "net_rx_bytes_per_sec": 0.0,
                "net_tx_bytes_per_sec": 0.0,
            }
        by_pod[uid]["net_rx_bytes_per_sec"] += r.net_rx_bytes_per_sec
        by_pod[uid]["net_tx_bytes_per_sec"] += r.net_tx_bytes_per_sec

    ranked = sorted(
        by_pod.values(),
        key=lambda x: max(x["net_rx_bytes_per_sec"], x["net_tx_bytes_per_sec"]),
        reverse=True,
    )
    return [
        _merge(
            pod_map,
            item["pod_uid"],
            {
                "net_rx_bytes_per_sec": round(item["net_rx_bytes_per_sec"], 1),
                "net_tx_bytes_per_sec": round(item["net_tx_bytes_per_sec"], 1),
                "net_rx_mbps": round(item["net_rx_bytes_per_sec"] / 1_048_576, 3),
                "net_tx_mbps": round(item["net_tx_bytes_per_sec"] / 1_048_576, 3),
            },
        )
        for item in ranked
    ]


@router.get("/disk")
def rank_disk(db: Session = Depends(get_db)):
    """All pods ranked by block IO throughput (read + write bytes/sec)."""
    pod_map = _pod_lookup(db)
    rows = latest_metric_per_pod(db, DockerMetric)

    by_pod: dict[str, dict] = {}
    for r in rows:
        uid = r.pod_uid
        if uid not in by_pod:
            by_pod[uid] = {
                "pod_uid": uid,
                "blk_read_bytes_per_sec": 0.0,
                "blk_write_bytes_per_sec": 0.0,
            }
        by_pod[uid]["blk_read_bytes_per_sec"] += r.blk_read_bytes_per_sec
        by_pod[uid]["blk_write_bytes_per_sec"] += r.blk_write_bytes_per_sec

    ranked = sorted(
        by_pod.values(),
        key=lambda x: x["blk_read_bytes_per_sec"] + x["blk_write_bytes_per_sec"],
        reverse=True,
    )
    return [
        _merge(
            pod_map,
            item["pod_uid"],
            {
                "blk_read_bytes_per_sec": round(item["blk_read_bytes_per_sec"], 1),
                "blk_write_bytes_per_sec": round(item["blk_write_bytes_per_sec"], 1),
                "blk_total_mbps": round(
                    (item["blk_read_bytes_per_sec"] + item["blk_write_bytes_per_sec"])
                    / 1_048_576,
                    3,
                ),
            },
        )
        for item in ranked
    ]


@router.get("/iops")
def rank_iops(db: Session = Depends(get_db)):
    """All pods ranked by IOPS (highest first)."""
    pod_map = _pod_lookup(db)
    rows = latest_metric_per_pod(db, DockerMetric)

    by_pod: dict[str, float] = {}
    for r in rows:
        by_pod[r.pod_uid] = by_pod.get(r.pod_uid, 0.0) + r.iops

    ranked = sorted(by_pod.items(), key=lambda x: x[1], reverse=True)
    return [
        _merge(pod_map, uid, {"iops": round(iops, 2)})
        for uid, iops in ranked
    ]


@router.get("/restarts")
def rank_restarts(db: Session = Depends(get_db)):
    """All pods ranked by total restart count (highest first)."""
    pods = (
        db.query(Pod)
        .order_by(Pod.restart_count.desc())
        .all()
    )
    return [
        {
            "uid": p.uid,
            "name": p.name,
            "namespace": p.namespace,
            "node_name": p.node_name,
            "phase": p.phase,
            "ready": p.ready,
            "restart_count": p.restart_count,
        }
        for p in pods
    ]


@router.get("/all")
def rank_all(db: Session = Depends(get_db)):
    """
    Combined payload returning all six ranking tables in one request.
    Useful for the initial dashboard load to populate all ranking panels.
    """
    pod_map = _pod_lookup(db)
    prom_rows = latest_metric_per_pod(db, PodMetric)
    docker_rows = latest_metric_per_pod(db, DockerMetric)

    # ------ CPU -------------------------------------------------------
    cpu_ranked = sorted(prom_rows, key=lambda r: r.cpu_pct_of_limit, reverse=True)
    cpu = [
        _merge(pod_map, r.pod_uid, {
            "cpu_pct_of_limit": round(r.cpu_pct_of_limit, 2),
            "cpu_usage_millicores": round(r.cpu_usage_millicores, 1),
        })
        for r in cpu_ranked
    ]

    # ------ Memory ----------------------------------------------------
    mem_ranked = sorted(prom_rows, key=lambda r: r.mem_pct_of_limit, reverse=True)
    memory = [
        _merge(pod_map, r.pod_uid, {
            "mem_pct_of_limit": round(r.mem_pct_of_limit, 2),
            "mem_usage_mb": round(r.mem_usage_bytes / 1_048_576, 1),
        })
        for r in mem_ranked
    ]

    # ------ Network ---------------------------------------------------
    net_by_pod: dict[str, dict] = {}
    for r in docker_rows:
        if r.pod_uid not in net_by_pod:
            net_by_pod[r.pod_uid] = {"rx": 0.0, "tx": 0.0}
        net_by_pod[r.pod_uid]["rx"] += r.net_rx_bytes_per_sec
        net_by_pod[r.pod_uid]["tx"] += r.net_tx_bytes_per_sec
    network = sorted(
        [
            _merge(pod_map, uid, {
                "net_rx_mbps": round(v["rx"] / 1_048_576, 3),
                "net_tx_mbps": round(v["tx"] / 1_048_576, 3),
            })
            for uid, v in net_by_pod.items()
        ],
        key=lambda x: max(x["net_rx_mbps"], x["net_tx_mbps"]),
        reverse=True,
    )

    # ------ Disk ------------------------------------------------------
    disk_by_pod: dict[str, dict] = {}
    for r in docker_rows:
        if r.pod_uid not in disk_by_pod:
            disk_by_pod[r.pod_uid] = {"rd": 0.0, "wr": 0.0}
        disk_by_pod[r.pod_uid]["rd"] += r.blk_read_bytes_per_sec
        disk_by_pod[r.pod_uid]["wr"] += r.blk_write_bytes_per_sec
    disk = sorted(
        [
            _merge(pod_map, uid, {
                "blk_total_mbps": round((v["rd"] + v["wr"]) / 1_048_576, 3),
                "blk_read_mbps": round(v["rd"] / 1_048_576, 3),
                "blk_write_mbps": round(v["wr"] / 1_048_576, 3),
            })
            for uid, v in disk_by_pod.items()
        ],
        key=lambda x: x["blk_total_mbps"],
        reverse=True,
    )

    # ------ IOPS ------------------------------------------------------
    iops_by_pod: dict[str, float] = {}
    for r in docker_rows:
        iops_by_pod[r.pod_uid] = iops_by_pod.get(r.pod_uid, 0.0) + r.iops
    iops = sorted(
        [_merge(pod_map, uid, {"iops": round(val, 2)}) for uid, val in iops_by_pod.items()],
        key=lambda x: x["iops"],
        reverse=True,
    )

    # ------ Restarts --------------------------------------------------
    pods_sorted = (
        db.query(Pod).order_by(Pod.restart_count.desc()).all()
    )
    restarts = [
        {
            "uid": p.uid,
            "name": p.name,
            "namespace": p.namespace,
            "node_name": p.node_name,
            "phase": p.phase,
            "ready": p.ready,
            "restart_count": p.restart_count,
        }
        for p in pods_sorted
    ]

    return {
        "cpu": cpu,
        "memory": memory,
        "network": network,
        "disk": disk,
        "iops": iops,
        "restarts": restarts,
    }
