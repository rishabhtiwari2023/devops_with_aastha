"""
Kubernetes collector.

Every `K8S_POLL_INTERVAL` seconds this module:

  1. Lists all Nodes -> upserts app.models.node.Node (status, capacity,
     conditions, taints, schedulable).
  2. Lists all Pods (all namespaces) -> upserts app.models.pod.Pod and its
     ContainerStatus children (phase, restart counts, last state/reason/
     exit code, resolved owner chain, PVC names).
  3. Diffs each pod's total restart count against what was previously
     stored -> writes app.models.events.RestartHistory rows whenever a
     restart is detected (this is the signal nearly every RCA rule keys
     off).
  4. Lists Events (all namespaces) -> upserts app.models.events.K8sEvent,
     deduplicated by the event's own UID, with count/last_seen bumped for
     recurring events.
  5. Updates each ContainerStatus's liveness/readiness probe-failure
     counters by counting recent "Unhealthy" events for that pod.

ReplicaSet -> Deployment ownership is resolved and cached in-process
(a pod owned by a ReplicaSet needs one extra lookup to find the
Deployment that owns the ReplicaSet; that mapping rarely changes so it's
cached rather than re-fetched every cycle).
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta

from kubernetes.client import ApiException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocalWrite
from app.collectors.k8s_client import get_core_v1, get_apps_v1
from app.models.node import Node
from app.models.pod import Pod, ContainerStatus
from app.models.events import K8sEvent, RestartHistory

logger = logging.getLogger("rca.k8s_collector")


def _iso_to_dt(ts):
    """Kubernetes python client already returns tz-aware datetimes; this
    just guards against None."""
    return ts if ts else None


class KubernetesCollector:
    def __init__(self):
        self.core = get_core_v1()
        self.apps = get_apps_v1()
        # ReplicaSet name+namespace -> Deployment name (cached; rebuilt lazily)
        self._rs_to_deployment_cache: dict[tuple[str, str], str] = {}

    # ------------------------------------------------------------------
    # Public entrypoint
    # ------------------------------------------------------------------
    async def collect_once(self):
        """Run one full collection pass. Safe to call concurrently with
        itself only if you accept duplicate work; the scheduler (Step 7)
        will call this serially on a timer."""
        # 1. Fetch data from K8s API server outside the DB transaction to prevent SQLite locking!
        try:
            node_list = await asyncio.to_thread(self.core.list_node)
            pod_list = await asyncio.to_thread(self.core.list_pod_for_all_namespaces, watch=False)
            event_list = await asyncio.to_thread(self.core.list_event_for_all_namespaces, watch=False)
            rs_list = await asyncio.to_thread(self.apps.list_replica_set_for_all_namespaces)
        except ApiException as e:
            logger.error("Kubernetes API error during collection: %s", e)
            return
        except Exception:
            logger.exception("Unexpected error fetching data from Kubernetes API")
            return

        # Pre-populate ReplicaSet -> Deployment owner cache
        for rs in rs_list.items:
            rs_name = rs.metadata.name
            ns = rs.metadata.namespace
            deployment_name = ""
            for ref in (rs.metadata.owner_references or []):
                if ref.kind == "Deployment":
                    deployment_name = ref.name
                    break
            self._rs_to_deployment_cache[(ns, rs_name)] = deployment_name

        # 2. Open DB session and execute all updates inside a short-lived transaction
        db = SessionLocalWrite()
        try:
            await asyncio.to_thread(self._collect_nodes, db, node_list)
            await asyncio.to_thread(self._collect_pods, db, pod_list)
            await asyncio.to_thread(self._collect_events, db, event_list)
            await asyncio.to_thread(self._update_probe_failure_counts, db)
            db.commit()
        except Exception:
            logger.exception("Unexpected error writing Kubernetes data to DB")
            db.rollback()
        finally:
            db.close()

    async def run_forever(self):
        logger.info("Kubernetes collector starting (interval=%ss)", settings.K8S_POLL_INTERVAL)
        while True:
            start = datetime.now(timezone.utc)
            await self.collect_once()
            elapsed = (datetime.now(timezone.utc) - start).total_seconds()
            await asyncio.sleep(max(0.0, settings.K8S_POLL_INTERVAL - elapsed))

    # ------------------------------------------------------------------
    # Nodes
    # ------------------------------------------------------------------
    def _collect_nodes(self, db: Session, node_list=None):
        if node_list is None:
            node_list = self.core.list_node()
        seen_names = set()

        for n in node_list.items:
            name = n.metadata.name
            seen_names.add(name)

            conditions = {c.type: c.status for c in (n.status.conditions or [])}
            ready = conditions.get("Ready", "Unknown")
            status = "Ready" if ready == "True" else ("NotReady" if ready == "False" else "Unknown")

            taints = [
                {"key": t.key, "value": t.value, "effect": t.effect}
                for t in (n.spec.taints or [])
            ] if n.spec.taints else []

            roles = ",".join(sorted(
                label.split("/")[-1]
                for label in (n.metadata.labels or {})
                if label.startswith("node-role.kubernetes.io/")
            )) or "worker"

            capacity = n.status.capacity or {}
            allocatable = n.status.allocatable or {}

            row = db.get(Node, name)
            if row is None:
                row = Node(name=name)
                db.add(row)

            row.status = status
            row.roles = roles
            row.kubelet_version = n.status.node_info.kubelet_version if n.status.node_info else ""
            row.os_image = n.status.node_info.os_image if n.status.node_info else ""
            row.architecture = n.status.node_info.architecture if n.status.node_info else ""
            row.container_runtime = n.status.node_info.container_runtime_version if n.status.node_info else ""
            row.cpu_capacity_millicores = _cpu_to_millicores(capacity.get("cpu"))
            row.cpu_allocatable_millicores = _cpu_to_millicores(allocatable.get("cpu"))
            row.mem_capacity_bytes = _mem_to_bytes(capacity.get("memory"))
            row.mem_allocatable_bytes = _mem_to_bytes(allocatable.get("memory"))
            row.conditions = conditions
            row.taints = taints
            row.schedulable = not bool(n.spec.unschedulable)
            row.last_updated = datetime.now(timezone.utc)

        # Update pod_count per node in a second pass (cheap: reuse pod list count)
        # done in _collect_pods after that method runs, to avoid a second full pod listing here.
        db.flush()

    # ------------------------------------------------------------------
    # Pods + Containers
    # ------------------------------------------------------------------
    def _collect_pods(self, db: Session, pod_list=None):
        if pod_list is None:
            pod_list = self.core.list_pod_for_all_namespaces(watch=False)
        node_pod_counts: dict[str, int] = {}

        for p in pod_list.items:
            uid = p.metadata.uid
            name = p.metadata.name
            namespace = p.metadata.namespace
            node_name = p.spec.node_name or ""
            if node_name:
                node_pod_counts[node_name] = node_pod_counts.get(node_name, 0) + 1

            owner_kind, owner_name, deployment, statefulset, daemonset, replicaset = \
                self._resolve_owner_chain(p, namespace)

            pvc_names = [
                v.persistent_volume_claim.claim_name
                for v in (p.spec.volumes or [])
                if v.persistent_volume_claim
            ]

            conditions = {
                c.type: c.status for c in (p.status.conditions or [])
            } if p.status and p.status.conditions else {}

            node_selector = p.spec.node_selector or {}
            tolerations = [
                {"key": t.key, "operator": t.operator, "value": t.value, "effect": t.effect}
                for t in (p.spec.tolerations or [])
            ] if p.spec.tolerations else []

            row = db.get(Pod, uid)
            is_new = row is None
            if is_new:
                row = Pod(uid=uid)
                db.add(row)

            previous_total_restarts = row.restart_count if not is_new else 0

            row.name = name
            row.namespace = namespace
            row.node_name = node_name
            row.phase = p.status.phase if p.status else "Unknown"
            row.status_reason = (p.status.reason or "") if p.status else ""
            row.pod_ip = (p.status.pod_ip or "") if p.status else ""
            row.qos_class = (p.status.qos_class or "") if p.status else ""
            row.owner_kind = owner_kind
            row.owner_name = owner_name
            row.deployment = deployment
            row.statefulset = statefulset
            row.daemonset = daemonset
            row.replicaset = replicaset
            row.pvc_names = pvc_names
            row.longhorn_volumes = []  # resolved by the Longhorn collector (Step 5)
            row.labels = dict(p.metadata.labels or {})
            row.conditions = conditions
            row.node_selector = node_selector
            row.tolerations = tolerations
            row.ready = conditions.get("Ready") == "True"
            row.created_at = _iso_to_dt(p.metadata.creation_timestamp)
            row.last_updated = datetime.now(timezone.utc)

            container_statuses = (p.status.container_statuses or []) if p.status else []
            total_restarts = sum(cs.restart_count for cs in container_statuses)
            row.restart_count = total_restarts

            db.flush()  # ensure row.uid is committed-visible for FK children

            self._sync_containers(db, row, container_statuses, p)

            # Restart detection: fire only when the pod's total restart count
            # actually grew since our last poll (not on first sight of a pod).
            if not is_new and total_restarts > previous_total_restarts:
                self._record_restart(db, row, container_statuses,
                                      previous_total_restarts, total_restarts)

        # Second pass: update node pod_count now that we've listed pods
        for node_name, count in node_pod_counts.items():
            node_row = db.get(Node, node_name)
            if node_row:
                node_row.pod_count = count

        db.flush()

    def _sync_containers(self, db: Session, pod_row: Pod, container_statuses, pod_obj):
        """Replace ContainerStatus rows wholesale for this pod (cheap: pods
        typically have 1-4 containers)."""
        existing = {c.container_name: c for c in pod_row.containers}
        seen_names = set()

        # Build quick lookup of resource requests/limits per container name
        limits_by_container = {}
        if pod_obj.spec and pod_obj.spec.containers:
            for c in pod_obj.spec.containers:
                res = c.resources
                cpu_limit = mem_limit = cpu_req = mem_req = 0.0
                if res:
                    if res.limits:
                        cpu_limit = _cpu_to_millicores(res.limits.get("cpu"))
                        mem_limit = _mem_to_bytes(res.limits.get("memory"))
                    if res.requests:
                        cpu_req = _cpu_to_millicores(res.requests.get("cpu"))
                        mem_req = _mem_to_bytes(res.requests.get("memory"))
                limits_by_container[c.name] = (cpu_limit, mem_limit, cpu_req, mem_req)

        for cs in container_statuses:
            seen_names.add(cs.name)
            crow = existing.get(cs.name)
            if crow is None:
                crow = ContainerStatus(pod_uid=pod_row.uid, container_name=cs.name)
                db.add(crow)

            state, state_reason, state_message = _parse_container_state(cs.state)
            last_state, last_state_reason, last_exit_code, last_finished_at = \
                _parse_last_state(cs.last_state)

            crow.image = cs.image or ""
            crow.ready = bool(cs.ready)
            crow.started = bool(cs.started) if cs.started is not None else state == "running"
            crow.restart_count = cs.restart_count
            crow.state = state
            crow.state_reason = state_reason
            crow.state_message = state_message
            crow.last_state = last_state
            crow.last_state_reason = last_state_reason
            crow.last_exit_code = last_exit_code
            crow.last_finished_at = last_finished_at

            cpu_limit, mem_limit, cpu_req, mem_req = limits_by_container.get(cs.name, (0, 0, 0, 0))
            crow.cpu_limit_millicores = cpu_limit
            crow.mem_limit_bytes = mem_limit
            crow.cpu_request_millicores = cpu_req
            crow.mem_request_bytes = mem_req
            crow.last_updated = datetime.now(timezone.utc)

        # Drop containers that no longer exist on the pod spec (rare: template change)
        for name, crow in existing.items():
            if name not in seen_names:
                db.delete(crow)

    def _record_restart(self, db: Session, pod_row: Pod, container_statuses,
                         previous_total, new_total):
        """Find which container's restart count grew and log why."""
        for cs in container_statuses:
            # We don't know the per-container previous count without a diff,
            # but pod-level restart_count growth combined with a terminated
            # `last_state` on this container is a strong enough signal to
            # attribute the restart to it. In practice with 1-2 restarts
            # between polls this is exact; with many simultaneous
            # container restarts all are logged.
            if cs.last_state and cs.last_state.terminated:
                term = cs.last_state.terminated
                db.add(RestartHistory(
                    pod_uid=pod_row.uid,
                    pod_name=pod_row.name,
                    namespace=pod_row.namespace,
                    node_name=pod_row.node_name,
                    container_name=cs.name,
                    restart_count=new_total,
                    previous_restart_count=previous_total,
                    last_state="terminated",
                    last_state_reason=term.reason or "",
                    exit_code=term.exit_code,
                ))
        logger.info("Restart detected: pod=%s/%s %d -> %d",
                    pod_row.namespace, pod_row.name, previous_total, new_total)

    # ------------------------------------------------------------------
    # Owner chain resolution (ReplicaSet -> Deployment)
    # ------------------------------------------------------------------
    def _resolve_owner_chain(self, pod, namespace):
        owner_kind = owner_name = deployment = statefulset = daemonset = replicaset = ""
        refs = pod.metadata.owner_references or []
        if not refs:
            return owner_kind, owner_name, deployment, statefulset, daemonset, replicaset

        ref = refs[0]
        owner_kind = ref.kind
        owner_name = ref.name

        if ref.kind == "StatefulSet":
            statefulset = ref.name
        elif ref.kind == "DaemonSet":
            daemonset = ref.name
        elif ref.kind == "ReplicaSet":
            replicaset = ref.name
            deployment = self._deployment_for_replicaset(namespace, ref.name)
        # Job/CronJob owners are left as owner_kind/owner_name only (not in the
        # brief's explicit Deployment/StatefulSet/DaemonSet/ReplicaSet list).

        return owner_kind, owner_name, deployment, statefulset, daemonset, replicaset

    def _deployment_for_replicaset(self, namespace: str, rs_name: str) -> str:
        key = (namespace, rs_name)
        if key in self._rs_to_deployment_cache:
            return self._rs_to_deployment_cache[key]
        return ""

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------
    def _collect_events(self, db: Session, event_list=None):
        events = event_list if event_list is not None else self.core.list_event_for_all_namespaces(watch=False)

        for ev in events.items:
            event_uid = ev.metadata.uid
            row = db.query(K8sEvent).filter(K8sEvent.event_uid == event_uid).one_or_none()

            involved = ev.involved_object
            node_name = ""
            if involved and involved.kind == "Pod":
                # best effort: pull the node from our own pod table
                pod_row = (
                    db.query(Pod)
                    .filter(Pod.name == involved.name, Pod.namespace == ev.metadata.namespace)
                    .order_by(Pod.last_updated.desc())
                    .first()
                )
                if pod_row:
                    node_name = pod_row.node_name or ""
            elif involved and involved.kind == "Node":
                node_name = involved.name

            if row is None:
                db.add(K8sEvent(
                    event_uid=event_uid,
                    namespace=ev.metadata.namespace or "",
                    involved_object_kind=involved.kind if involved else "",
                    involved_object_name=involved.name if involved else "",
                    node_name=node_name,
                    reason=ev.reason or "",
                    message=ev.message or "",
                    event_type=ev.type or "Normal",
                    source_component=(ev.source.component if ev.source else "") or "",
                    count=ev.count or 1,
                    first_seen=_iso_to_dt(ev.first_timestamp) or _iso_to_dt(ev.event_time),
                    last_seen=_iso_to_dt(ev.last_timestamp) or datetime.now(timezone.utc),
                ))
            else:
                row.count = ev.count or row.count
                row.last_seen = _iso_to_dt(ev.last_timestamp) or datetime.now(timezone.utc)
                row.message = ev.message or row.message

        db.flush()

    # ------------------------------------------------------------------
    # Probe failure counters (derived from Unhealthy events)
    # ------------------------------------------------------------------
    def _update_probe_failure_counts(self, db: Session):
        cutoff = datetime.now(timezone.utc) - timedelta(
            minutes=settings.PROBE_FAILURE_LOOKBACK_MINUTES)

        unhealthy_events = db.query(K8sEvent).filter(
            K8sEvent.reason == "Unhealthy",
            K8sEvent.last_seen >= cutoff,
            K8sEvent.involved_object_kind == "Pod",
        ).all()

        # namespace/pod_name -> {"liveness": n, "readiness": n}
        tally: dict[tuple[str, str], dict[str, int]] = {}
        for ev in unhealthy_events:
            key = (ev.namespace, ev.involved_object_name)
            bucket = tally.setdefault(key, {"liveness": 0, "readiness": 0})
            msg = (ev.message or "").lower()
            if "liveness" in msg:
                bucket["liveness"] += ev.count
            if "readiness" in msg:
                bucket["readiness"] += ev.count

        for (namespace, pod_name), counts in tally.items():
            pod_row = (
                db.query(Pod)
                .filter(Pod.name == pod_name, Pod.namespace == namespace)
                .order_by(Pod.last_updated.desc())
                .first()
            )
            if not pod_row:
                continue
            for crow in pod_row.containers:
                crow.liveness_probe_failures = counts["liveness"]
                crow.readiness_probe_failures = counts["readiness"]

        db.flush()


# ----------------------------------------------------------------------
# Unit conversion helpers
# ----------------------------------------------------------------------
def _cpu_to_millicores(v) -> float:
    """Convert a Kubernetes CPU quantity ('500m', '2', '2.5') to millicores."""
    if not v:
        return 0.0
    v = str(v)
    try:
        if v.endswith("m"):
            return float(v[:-1])
        return float(v) * 1000.0
    except ValueError:
        return 0.0


_MEM_UNITS = {
    "Ki": 1024, "Mi": 1024**2, "Gi": 1024**3, "Ti": 1024**4,
    "K": 1000, "M": 1000**2, "G": 1000**3, "T": 1000**4,
}


def _mem_to_bytes(v) -> float:
    """Convert a Kubernetes memory quantity ('512Mi', '2Gi', '1000000') to bytes."""
    if not v:
        return 0.0
    v = str(v)
    for suffix, mult in _MEM_UNITS.items():
        if v.endswith(suffix):
            try:
                return float(v[:-len(suffix)]) * mult
            except ValueError:
                return 0.0
    try:
        return float(v)
    except ValueError:
        return 0.0


def _parse_container_state(state):
    """Returns (state_name, reason, message) for a V1ContainerState."""
    if state is None:
        return "unknown", "", ""
    if state.running:
        return "running", "", ""
    if state.waiting:
        return "waiting", state.waiting.reason or "", state.waiting.message or ""
    if state.terminated:
        return "terminated", state.terminated.reason or "", state.terminated.message or ""
    return "unknown", "", ""


def _parse_last_state(last_state):
    """Returns (state_name, reason, exit_code, finished_at) for a
    V1ContainerState representing the previous run of the container."""
    if last_state is None:
        return "", "", None, None
    if last_state.terminated:
        t = last_state.terminated
        return "terminated", t.reason or "", t.exit_code, _iso_to_dt(t.finished_at)
    if last_state.waiting:
        return "waiting", last_state.waiting.reason or "", None, None
    if last_state.running:
        return "running", "", None, None
    return "", "", None, None
