"""
Longhorn collector.

Every `LONGHORN_POLL_INTERVAL` seconds this module:

  1. Fetches /v1/volumes, /v1/replicas, /v1/engines from the Longhorn
     Manager API and joins them into one row per volume.
  2. Writes a LonghornVolumeMetric snapshot per volume (state, robustness,
     attached node, replica states, rebuild-in-progress flag, capacity).
  3. Diffs each volume's (state, robustness, rebuild_in_progress) against
     what was recorded on the *previous* poll and, on a transition, writes
     a synthetic K8sEvent row (involved_object_kind="Volume") so these
     shifts show up on the same timeline as Kubernetes events - this is
     what feeds the "Longhorn Replica Rebuild causing Disk Pressure" style
     RCA rules, since Longhorn's own /v1/events endpoint isn't available
     on every manager version.
  4. Resolves each volume's PVC name back to the owning Pod(s) (via
     Pod.pvc_names, populated by the Kubernetes collector) and keeps
     Pod.longhorn_volumes in sync so the UI can show "this pod's volume is
     degraded" without a second lookup.

Longhorn's REST API does not expose live throughput/IOPS/latency figures
on the volume/replica/engine objects themselves (those live in the
longhorn-manager's own Prometheus metrics endpoint, e.g.
longhorn_volume_read_throughput). Rather than silently guessing, this
collector leaves those four fields at 0 unless `LONGHORN_METRICS_URL` is
configured to point at that Prometheus-format endpoint, in which case
`_read_longhorn_metrics` scrapes and merges them in.
"""

import asyncio
import logging
import re
from datetime import datetime, timezone

import aiohttp
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal, SessionLocalWrite
from app.collectors.longhorn_client import get_volumes, get_replicas, get_engines
from app.models.longhorn import LonghornVolumeMetric
from app.models.events import K8sEvent
from app.models.pod import Pod

logger = logging.getLogger("rca.longhorn_collector")


class LonghornCollector:
    def __init__(self):
        # volume_name -> {"state":, "robustness":, "rebuild_in_progress":}
        self._prev_state: dict[str, dict] = {}

    # ------------------------------------------------------------------
    async def collect_once(self):
        async with aiohttp.ClientSession() as session:
            volumes, replicas, engines, metrics = await asyncio.gather(
                get_volumes(session),
                get_replicas(session),
                get_engines(session),
                _read_longhorn_metrics(session),
            )

        rows = _merge(volumes, replicas, engines, metrics)

        db = SessionLocalWrite()
        try:
            self._write_metrics(db, rows)
            self._detect_transitions(db, rows)
            self._sync_pod_volume_links(db, rows)
            db.commit()
        except Exception:
            logger.exception("Failed writing Longhorn metrics to DB")
            db.rollback()
        finally:
            db.close()

    async def run_forever(self):
        logger.info("Longhorn collector starting (interval=%ss)", settings.LONGHORN_POLL_INTERVAL)
        while True:
            start = datetime.now(timezone.utc)
            await self.collect_once()
            elapsed = (datetime.now(timezone.utc) - start).total_seconds()
            await asyncio.sleep(max(0.0, settings.LONGHORN_POLL_INTERVAL - elapsed))

    # ------------------------------------------------------------------
    def _write_metrics(self, db: Session, rows: list[dict]):
        for row in rows:
            db.add(LonghornVolumeMetric(
                volume_name=row["volume_name"],
                pvc_name=row["pvc_name"],
                pv_name=row["pv_name"],
                namespace=row["namespace"],
                state=row["state"],
                robustness=row["robustness"],
                attached_node=row["attached_node"],
                engine_name=row["engine_name"],
                engine_state=row["engine_state"],
                replica_count=len(row["replica_states"]),
                replica_states=row["replica_states"],
                rebuild_in_progress=row["rebuild_in_progress"],
                scheduled_size_bytes=row["scheduled_size_bytes"],
                actual_size_bytes=row["actual_size_bytes"],
                available_space_bytes=row["available_space_bytes"],
                read_iops=row["read_iops"],
                write_iops=row["write_iops"],
                read_throughput_bytes_per_sec=row["read_throughput_bytes_per_sec"],
                write_throughput_bytes_per_sec=row["write_throughput_bytes_per_sec"],
                read_latency_ms=row["read_latency_ms"],
                write_latency_ms=row["write_latency_ms"],
            ))
        db.flush()

    # ------------------------------------------------------------------
    def _detect_transitions(self, db: Session, rows: list[dict]):
        """Synthesize Warning-type K8sEvent rows whenever a volume's
        state/robustness/rebuild flag changes, so the RCA engine and
        timeline see Longhorn activity the same way they see native
        Kubernetes events."""
        seen_volumes = set()

        for row in rows:
            name = row["volume_name"]
            seen_volumes.add(name)
            prev = self._prev_state.get(name)
            curr = {
                "state": row["state"],
                "robustness": row["robustness"],
                "rebuild_in_progress": row["rebuild_in_progress"],
            }

            if prev is not None:
                if prev["robustness"] != curr["robustness"] and curr["robustness"] in ("degraded", "faulted"):
                    self._emit_volume_event(db, row, "VolumeDegraded" if curr["robustness"] == "degraded"
                                             else "VolumeFaulted",
                                             f"Volume {name} robustness changed "
                                             f"{prev['robustness']} -> {curr['robustness']}")
                if not prev["rebuild_in_progress"] and curr["rebuild_in_progress"]:
                    self._emit_volume_event(db, row, "VolumeReplicaRebuildStarted",
                                             f"Replica rebuild started for volume {name}")
                if prev["rebuild_in_progress"] and not curr["rebuild_in_progress"]:
                    self._emit_volume_event(db, row, "VolumeReplicaRebuildFinished",
                                             f"Replica rebuild finished for volume {name}")
                if prev["state"] == "attached" and curr["state"] == "detached":
                    self._emit_volume_event(db, row, "VolumeDetached",
                                             f"Volume {name} detached from {row['attached_node'] or 'node'}")
                if prev["state"] != "attached" and curr["state"] == "attached":
                    self._emit_volume_event(db, row, "VolumeAttached",
                                             f"Volume {name} attached to {row['attached_node'] or 'node'}")

            self._prev_state[name] = curr

        # Drop cached state for volumes that no longer exist (deleted PVC/volume)
        for stale in set(self._prev_state) - seen_volumes:
            self._prev_state.pop(stale, None)

        db.flush()

    def _emit_volume_event(self, db: Session, row: dict, reason: str, message: str):
        db.add(K8sEvent(
            event_uid=f"longhorn-{row['volume_name']}-{reason}-{int(datetime.now(timezone.utc).timestamp())}",
            namespace=row["namespace"],
            involved_object_kind="Volume",
            involved_object_name=row["volume_name"],
            node_name=row["attached_node"],
            reason=reason,
            message=message,
            event_type="Warning" if reason in (
                "VolumeDegraded", "VolumeFaulted", "VolumeDetached",
            ) else "Normal",
            source_component="longhorn-manager",
            count=1,
            first_seen=datetime.now(timezone.utc),
            last_seen=datetime.now(timezone.utc),
        ))
        logger.info("Longhorn transition: %s - %s", reason, message)

    # ------------------------------------------------------------------
    def _sync_pod_volume_links(self, db: Session, rows: list[dict]):
        """Keep Pod.longhorn_volumes in sync with the PVC -> volume mapping
        we just observed, so the UI's per-pod panel can show volume health
        without a join at render time."""
        pvc_to_volume: dict[str, str] = {
            row["pvc_name"]: row["volume_name"] for row in rows if row["pvc_name"]
        }
        if not pvc_to_volume:
            return

        read_db = SessionLocal()
        try:
            pod_lookups = read_db.query(Pod.uid, Pod.pvc_names, Pod.longhorn_volumes).filter(Pod.pvc_names.isnot(None)).all()
        finally:
            read_db.close()

        for uid, pvc_names, longhorn_volumes in pod_lookups:
            pvc_names_list = pvc_names or []
            resolved = [pvc_to_volume[p] for p in pvc_names_list if p in pvc_to_volume]
            if resolved != (longhorn_volumes or []):
                # Fetch actual instance from write db session only for mutation
                pod_to_update = db.query(Pod).get(uid)
                if pod_to_update:
                    pod_to_update.longhorn_volumes = resolved
        db.flush()


# ----------------------------------------------------------------------
# Merge helpers
# ----------------------------------------------------------------------
def _merge(volumes: list[dict], replicas: list[dict], engines: list[dict],
           metrics: dict) -> list[dict]:
    replicas_by_volume: dict[str, list[dict]] = {}
    for r in replicas:
        vol_name = r.get("volumeName") or r.get("spec", {}).get("volumeName", "")
        replicas_by_volume.setdefault(vol_name, []).append(r)

    engines_by_volume: dict[str, dict] = {}
    for e in engines:
        vol_name = e.get("volumeName") or e.get("spec", {}).get("volumeName", "")
        engines_by_volume[vol_name] = e

    rows = []
    for v in volumes:
        name = v.get("name", "")
        k8s_status = v.get("kubernetesStatus", {}) or {}
        pvc_name = k8s_status.get("pvcName", "") or ""
        namespace = k8s_status.get("namespace", "") or ""

        controllers = v.get("controllers") or []
        attached_node = controllers[0].get("hostId", "") if controllers else ""

        engine = engines_by_volume.get(name, {})
        if not engine and controllers:
            engine = controllers[0]

        engine_name = engine.get("name", "")
        engine_state = engine.get("state", "") or v.get("state", "")

        vol_replicas = replicas_by_volume.get(name, [])
        if not vol_replicas:
            vol_replicas = v.get("replicas") or []

        replica_states = [
            {
                "name": r.get("name", ""),
                "node": r.get("hostId", ""),
                "mode": r.get("mode", ""),          # RW / WO (rebuilding) / ERR
                "running": bool(r.get("running", False)),
                "state": r.get("currentState") or r.get("state") or ("running" if r.get("running") else "stopped"),
            }
            for r in vol_replicas
        ]
        rebuild_in_progress = any(rs["mode"] == "WO" for rs in replica_states) or \
            bool(v.get("rebuildStatus"))

        rows.append({
            "volume_name": name,
            "pvc_name": pvc_name,
            "pv_name": k8s_status.get("pvName", "") or "",
            "namespace": namespace,
            "state": v.get("state", "") or "",
            "robustness": v.get("robustness", "") or "",
            "attached_node": attached_node,
            "engine_name": engine_name,
            "engine_state": engine_state,
            "replica_states": replica_states,
            "rebuild_in_progress": rebuild_in_progress,
            "scheduled_size_bytes": _to_float(v.get("size")),
            "actual_size_bytes": _to_float(v.get("actualSize")),
            "available_space_bytes": _to_float(v.get("availableSpace")),
            **metrics.get(name, _EMPTY_METRICS),
        })
    return rows


_EMPTY_METRICS = {
    "read_iops": 0.0, "write_iops": 0.0,
    "read_throughput_bytes_per_sec": 0.0, "write_throughput_bytes_per_sec": 0.0,
    "read_latency_ms": 0.0, "write_latency_ms": 0.0,
}


def _to_float(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


# ----------------------------------------------------------------------
# Optional: scrape longhorn-manager's Prometheus-format metrics endpoint
# for throughput/IOPS/latency, which aren't in the /v1 REST objects.
# ----------------------------------------------------------------------
_METRIC_LINE_RE = re.compile(
    r'^(?P<name>longhorn_volume_\w+)\{[^}]*volume="(?P<volume>[^"]+)"[^}]*\}\s+(?P<value>[0-9.eE+-]+)'
)

_METRIC_FIELD_MAP = {
    "longhorn_volume_read_iops": "read_iops",
    "longhorn_volume_write_iops": "write_iops",
    "longhorn_volume_read_throughput": "read_throughput_bytes_per_sec",
    "longhorn_volume_write_throughput": "write_throughput_bytes_per_sec",
    "longhorn_volume_read_latency": "read_latency_ms",
    "longhorn_volume_write_latency": "write_latency_ms",
}


async def _read_longhorn_metrics(session: aiohttp.ClientSession) -> dict:
    """Scrape LONGHORN_METRICS_URL (Prometheus text exposition format) if
    configured. Returns {} (and every volume falls back to zeros) when
    unset or unreachable - this is a best-effort enrichment, not a
    required data source."""
    url = getattr(settings, "LONGHORN_METRICS_URL", "") or ""
    if not url:
        return {}

    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                return {}
            text = await resp.text()
    except (aiohttp.ClientError, TimeoutError) as e:
        logger.debug("Longhorn metrics endpoint unreachable: %s", e)
        return {}

    result: dict[str, dict] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        m = _METRIC_LINE_RE.match(line)
        if not m:
            continue
        field = _METRIC_FIELD_MAP.get(m.group("name"))
        if not field:
            continue
        volume = m.group("volume")
        try:
            value = float(m.group("value"))
        except ValueError:
            continue
        bucket = result.setdefault(volume, dict(_EMPTY_METRICS))
        bucket[field] = value
    return result
