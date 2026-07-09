"""
Rule-Based RCA Correlation Engine - orchestrator.

Every `RCA_EVAL_INTERVAL` seconds this module:

  1. Finds "triggers" worth evaluating: pods whose restart count just
     increased (app.models.events.RestartHistory), plus pods currently
     Pending, NotReady, or Evicted that haven't already been alerted on
     recently.
  2. For each trigger, gathers `Evidence` (app.rca.rules.Evidence) from
     every source table the collectors have been filling in: PodMetric,
     NodeMetric, DockerMetric, LonghornVolumeMetric, Node conditions,
     ContainerStatus probe-failure counters, and recent K8sEvents.
  3. Runs `app.rca.rules.RULES` in order and takes the first match (there
     is always a match - `rule_unexplained` is a catch-all).
  4. Writes one `RootCauseRecord` + one `Alert` per incident.

Dedup: a restart is only ever evaluated once - its RestartHistory row id
is remembered in an in-process set the same way the Docker/Longhorn
collectors remember previous-sample state. Non-restart triggers (Pending/
NotReady/Evicted, which don't have a discrete "new row" to key off) use a
per-(pod, condition) cooldown instead, so an unresolved condition doesn't
re-alert every poll cycle.
"""

import asyncio
import logging
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Optional

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocalWrite
from app.models.pod import Pod, ContainerStatus
from app.models.node import Node
from app.models.metrics import PodMetric, NodeMetric, DockerMetric
from app.models.longhorn import LonghornVolumeMetric
from app.models.events import K8sEvent, RestartHistory
from app.models.rca import RootCauseRecord
from app.models.alerts import Alert
from app.rca.rules import Evidence, RuleResult, RULES

logger = logging.getLogger("rca.engine")

_VOLUME_EVENT_REASONS = {
    "VolumeDegraded", "VolumeFaulted", "VolumeReplicaRebuildStarted",
    "VolumeReplicaRebuildFinished", "VolumeDetached", "VolumeAttached",
}


class Trigger:
    __slots__ = ("pod_uid", "kind", "timestamp", "restart_row")

    def __init__(self, pod_uid: str, kind: str, timestamp: datetime, restart_row=None):
        self.pod_uid = pod_uid
        self.kind = kind              # "restart" | "pending" | "notready" | "evicted"
        self.timestamp = timestamp
        self.restart_row = restart_row


class RCAEngine:
    def __init__(self):
        self._processed_restart_ids: set[int] = set()
        # (pod_uid, kind) -> last time we alerted on it
        self._last_alert_at: dict[tuple, datetime] = {}
        # Optional hook for the WebSocket broadcaster (wired up in Step 7)
        # to get pushed each new (root_cause, alert) pair without this
        # module needing to know anything about WebSockets.
        self.on_alert: Optional[Callable[[RootCauseRecord, Alert], None]] = None

    # ------------------------------------------------------------------
    async def collect_once(self):
        attempts = 5
        backoff = 0.2
        for attempt in range(1, attempts + 1):
            db = SessionLocalWrite()
            try:
                triggers = self._find_triggers(db)
                for trig in triggers:
                    evidence = self._build_evidence(db, trig)
                    if evidence is None:
                        continue
                    result = _evaluate(evidence)
                    await self._persist(db, evidence, result)
                db.commit()
                return  # Success
            except OperationalError as exc:
                db.rollback()
                orig = getattr(exc, 'orig', None)
                if isinstance(orig, sqlite3.OperationalError) and 'database is locked' in str(orig).lower():
                    if attempt == attempts:
                        raise
                    logger.warning("SQLite locked during RCA collect_once; retrying %s/%s after %.1fs",
                                   attempt, attempts, backoff)
                    await asyncio.sleep(backoff)
                    backoff *= 2
                else:
                    logger.exception("OperationalError in RCA engine")
                    raise
            except Exception:
                db.rollback()
                logger.exception("Unexpected error in RCA engine")
                raise
            finally:
                db.close()

    async def run_forever(self):
        logger.info("RCA engine starting (interval=%ss)", settings.RCA_EVAL_INTERVAL)
        while True:
            start = datetime.now(timezone.utc)
            await self.collect_once()
            elapsed = (datetime.now(timezone.utc) - start).total_seconds()
            await asyncio.sleep(max(0.0, settings.RCA_EVAL_INTERVAL - elapsed))

    # ------------------------------------------------------------------
    # Trigger discovery
    # ------------------------------------------------------------------
    def _find_triggers(self, db: Session) -> list[Trigger]:
        triggers: list[Trigger] = []
        now = datetime.now(timezone.utc)
        restart_lookback = now - timedelta(minutes=settings.RESTART_WINDOW_MINUTES)
        cooldown = timedelta(minutes=settings.ALERT_DEDUP_COOLDOWN_MINUTES)

        # -- Discrete restart events --------------------------------------
        recent_restarts = db.query(RestartHistory).filter(
            RestartHistory.timestamp >= restart_lookback
        ).all()
        for r in recent_restarts:
            if r.id in self._processed_restart_ids:
                continue
            self._processed_restart_ids.add(r.id)
            triggers.append(Trigger(pod_uid=r.pod_uid, kind="restart", timestamp=r.timestamp,
                                     restart_row=r))

        # Bound memory growth of the processed-id set.
        if len(self._processed_restart_ids) > 50_000:
            self._processed_restart_ids.clear()

        # -- Ongoing conditions (cooldown-deduped) --------------------------
        stuck_pods = db.query(Pod).filter(
            Pod.phase.in_(["Pending", "Failed"])
        ).all()
        for pod in stuck_pods:
            kind = "evicted" if pod.status_reason == "Evicted" else "pending"
            key = (pod.uid, kind)
            last = self._last_alert_at.get(key)
            if last and now - last < cooldown:
                continue
            self._last_alert_at[key] = now
            triggers.append(Trigger(pod_uid=pod.uid, kind=kind, timestamp=now))

        notready_nodes = {n.name for n in db.query(Node).filter(Node.status == "NotReady").all()}
        if notready_nodes:
            affected_pods = db.query(Pod).filter(Pod.node_name.in_(notready_nodes)).all()
            for pod in affected_pods:
                key = (pod.uid, "notready")
                last = self._last_alert_at.get(key)
                if last and now - last < cooldown:
                    continue
                self._last_alert_at[key] = now
                triggers.append(Trigger(pod_uid=pod.uid, kind="notready", timestamp=now))

        return triggers

    # ------------------------------------------------------------------
    # Evidence gathering
    # ------------------------------------------------------------------
    def _build_evidence(self, db: Session, trig: Trigger) -> Optional[Evidence]:
        pod = db.get(Pod, trig.pod_uid)
        if pod is None:
            return None

        node = db.get(Node, pod.node_name) if pod.node_name else None
        node_metric = (
            db.query(NodeMetric)
            .filter(NodeMetric.node_name == pod.node_name)
            .order_by(NodeMetric.timestamp.desc())
            .first()
            if pod.node_name else None
        )
        pod_metric = (
            db.query(PodMetric)
            .filter(PodMetric.pod_uid == pod.uid)
            .order_by(PodMetric.timestamp.desc())
            .first()
        )

        net_rx, net_tx, blk_read, blk_write, iops = _aggregate_docker_metrics(db, pod.uid)

        last_state_reason = ""
        exit_code = None
        previous_restart_count = pod.restart_count
        new_restart_count = pod.restart_count
        if trig.restart_row is not None:
            last_state_reason = trig.restart_row.last_state_reason or ""
            exit_code = trig.restart_row.exit_code
            previous_restart_count = trig.restart_row.previous_restart_count
            new_restart_count = trig.restart_row.restart_count
        else:
            # No discrete restart row for this trigger - fall back to the
            # most recent container state if one looks abnormal.
            for c in pod.containers:
                if c.last_state_reason:
                    last_state_reason = c.last_state_reason
                    exit_code = c.last_exit_code
                    break

        readiness_failures = sum(c.readiness_probe_failures for c in pod.containers)
        liveness_failures = sum(c.liveness_probe_failures for c in pod.containers)

        longhorn_volumes = _latest_volume_states(db, pod.longhorn_volumes or [])
        recent_volume_events = _recent_volume_event_reasons(db, pod.longhorn_volumes or [])

        conditions = (node.conditions or {}) if node else {}
        failed_scheduling = db.query(K8sEvent).filter(
            K8sEvent.involved_object_name == pod.name,
            K8sEvent.namespace == pod.namespace,
            K8sEvent.reason == "FailedScheduling",
            K8sEvent.last_seen >= trig.timestamp - timedelta(minutes=settings.RESTART_WINDOW_MINUTES),
        ).first() is not None

        return Evidence(
            pod_uid=pod.uid,
            pod_name=pod.name,
            namespace=pod.namespace,
            node_name=pod.node_name or "",
            trigger=trig.kind,
            trigger_timestamp=trig.timestamp,
            pod_phase=pod.phase,
            pod_status_reason=pod.status_reason or "",
            restart_count_increased=trig.kind == "restart",
            previous_restart_count=previous_restart_count,
            new_restart_count=new_restart_count,
            last_state_reason=last_state_reason,
            exit_code=exit_code,
            cpu_pct_of_limit=pod_metric.cpu_pct_of_limit if pod_metric else 0.0,
            mem_pct_of_limit=pod_metric.mem_pct_of_limit if pod_metric else 0.0,
            mem_usage_bytes=pod_metric.mem_usage_bytes if pod_metric else 0.0,
            mem_limit_bytes=pod_metric.mem_limit_bytes if pod_metric else 0.0,
            node_cpu_pct=node_metric.cpu_pct if node_metric else 0.0,
            node_mem_pct=node_metric.mem_pct if node_metric else 0.0,
            node_disk_pct=node_metric.disk_pct if node_metric else 0.0,
            node_disk_pressure=(conditions.get("DiskPressure") == "True")
            or bool(node_metric and node_metric.disk_pct >= settings.NODE_DISK_PRESSURE_PCT),
            node_memory_pressure=conditions.get("MemoryPressure") == "True",
            node_status=node.status if node else "Unknown",
            net_rx_bytes_per_sec=net_rx,
            net_tx_bytes_per_sec=net_tx,
            blk_read_bytes_per_sec=blk_read,
            blk_write_bytes_per_sec=blk_write,
            iops=iops,
            readiness_probe_failures=readiness_failures,
            liveness_probe_failures=liveness_failures,
            longhorn_volumes=longhorn_volumes,
            recent_volume_events=recent_volume_events,
            failed_scheduling=failed_scheduling,
            evicted=(pod.status_reason == "Evicted"),
        )

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    async def _persist(self, db: Session, ev: Evidence, result: RuleResult):
        rc = RootCauseRecord(
            pod_uid=ev.pod_uid,
            pod_name=ev.pod_name,
            namespace=ev.namespace,
            node_name=ev.node_name,
            category=result.category,
            reason_code=result.reason_code,
            severity=result.severity,
            short_reason=result.short_reason,
            explanation=result.explanation,
            evidence=result.evidence,
        )
        db.add(rc)
        db.flush()

        alert = Alert(
            severity=result.severity,
            source="rca",
            pod_name=ev.pod_name,
            namespace=ev.namespace,
            node_name=ev.node_name,
            title=result.short_reason,
            message=result.explanation,
            root_cause_id=rc.id,
        )
        db.add(alert)
        db.flush()

        logger.info("RCA verdict: pod=%s/%s trigger=%s -> %s (%s)",
                    ev.namespace, ev.pod_name, ev.trigger, result.reason_code, result.severity)

        if self.on_alert:
            try:
                self.on_alert(rc, alert)
            except Exception:
                logger.exception("on_alert callback failed")




def _evaluate(ev: Evidence) -> RuleResult:
    for rule in RULES:
        result = rule(ev)
        if result is not None:
            return result
    # Unreachable in practice - rule_unexplained always matches - but keep
    # a hard fallback so the engine can never crash on an empty RULES list.
    return RuleResult(
        category="unknown", reason_code="UNEXPLAINED", severity="info",
        short_reason="Cause Not Yet Determined",
        explanation=f"{ev.pod_name} had a '{ev.trigger}' incident with no matching rule.",
        evidence={},
    )


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _aggregate_docker_metrics(db: Session, pod_uid: str) -> tuple:
    """Sum the latest-per-container Docker rates for a pod. Each container
    reports independently, so we take the most recent row per
    container_id, then sum across containers to get the pod's total."""
    rows = (
        db.query(DockerMetric)
        .filter(DockerMetric.pod_uid == pod_uid)
        .order_by(DockerMetric.timestamp.desc())
        .limit(50)   # small pods have few containers; 50 rows covers several polls
        .all()
    )
    latest_per_container: dict[str, DockerMetric] = {}
    for row in rows:
        if row.container_id not in latest_per_container:
            latest_per_container[row.container_id] = row

    net_rx = sum(r.net_rx_bytes_per_sec for r in latest_per_container.values())
    net_tx = sum(r.net_tx_bytes_per_sec for r in latest_per_container.values())
    blk_read = sum(r.blk_read_bytes_per_sec for r in latest_per_container.values())
    blk_write = sum(r.blk_write_bytes_per_sec for r in latest_per_container.values())
    iops = sum(r.iops for r in latest_per_container.values())
    return net_rx, net_tx, blk_read, blk_write, iops


def _latest_volume_states(db: Session, volume_names: list[str]) -> list[dict]:
    result = []
    for name in volume_names:
        row = (
            db.query(LonghornVolumeMetric)
            .filter(LonghornVolumeMetric.volume_name == name)
            .order_by(LonghornVolumeMetric.timestamp.desc())
            .first()
        )
        if row:
            result.append({
                "volume_name": row.volume_name,
                "rebuild_in_progress": row.rebuild_in_progress,
                "robustness": row.robustness,
                "state": row.state,
            })
    return result


def _recent_volume_event_reasons(db: Session, volume_names: list[str]) -> list[str]:
    if not volume_names:
        return []
    lookback = datetime.now(timezone.utc) - timedelta(minutes=settings.RESTART_WINDOW_MINUTES)
    rows = db.query(K8sEvent).filter(
        K8sEvent.involved_object_kind == "Volume",
        K8sEvent.involved_object_name.in_(volume_names),
        K8sEvent.reason.in_(_VOLUME_EVENT_REASONS),
        K8sEvent.last_seen >= lookback,
    ).all()
    return [r.reason for r in rows]
