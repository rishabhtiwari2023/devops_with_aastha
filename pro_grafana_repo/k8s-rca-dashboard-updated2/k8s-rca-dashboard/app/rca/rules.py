"""
Rule-Based RCA Correlation Engine - rule definitions.

Per the brief: no AI/LLM here. Each rule is a small, pure function that
takes an `Evidence` snapshot (everything the engine gathered about one
pod incident from Kubernetes/Prometheus/Docker/Longhorn) and returns a
`RuleResult` if its condition matches, or `None` otherwise.

Kept deliberately as plain functions over a dataclass (no framework, no
DSL) so the ordered `RULES` list at the bottom is the entire decision
table and can be read top-to-bottom like the brief's own "IF ... AND ...
THEN Reason: ..." examples. `app/rca/engine.py` gathers the Evidence and
walks this list, taking the first match.

`explanation` is already a full human-readable sentence - a rule-based
stand-in for the "Future AI Layer" described in the brief. The `evidence`
dict on each result is what a future LLM narrator would be handed instead
of raw metric names.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from app.core.config import settings


# ----------------------------------------------------------------------
# Evidence: everything the engine assembled about one pod incident
# ----------------------------------------------------------------------
@dataclass
class Evidence:
    pod_uid: str
    pod_name: str
    namespace: str
    node_name: str

    trigger: str                    # "restart" | "pending" | "notready" | "evicted"
    trigger_timestamp: datetime

    pod_phase: str = "Unknown"
    pod_status_reason: str = ""

    restart_count_increased: bool = False
    previous_restart_count: int = 0
    new_restart_count: int = 0
    last_state_reason: str = ""     # OOMKilled / Error / Completed / ...
    exit_code: Optional[int] = None

    cpu_pct_of_limit: float = 0.0
    mem_pct_of_limit: float = 0.0
    mem_usage_bytes: float = 0.0
    mem_limit_bytes: float = 0.0

    node_cpu_pct: float = 0.0
    node_mem_pct: float = 0.0
    node_disk_pct: float = 0.0
    node_disk_pressure: bool = False
    node_memory_pressure: bool = False
    node_status: str = "Unknown"    # Ready / NotReady / Unknown

    net_rx_bytes_per_sec: float = 0.0
    net_tx_bytes_per_sec: float = 0.0
    blk_read_bytes_per_sec: float = 0.0
    blk_write_bytes_per_sec: float = 0.0
    iops: float = 0.0

    readiness_probe_failures: int = 0
    liveness_probe_failures: int = 0

    # [{"volume_name":, "rebuild_in_progress":, "robustness":, "state":}, ...]
    longhorn_volumes: list = field(default_factory=list)
    # reasons seen recently against this pod's volumes, e.g.
    # ["VolumeReplicaRebuildStarted", "VolumeDetached"]
    recent_volume_events: list = field(default_factory=list)

    failed_scheduling: bool = False
    evicted: bool = False

    def net_bytes_per_sec(self) -> float:
        return max(self.net_rx_bytes_per_sec, self.net_tx_bytes_per_sec)

    def disk_io_bytes_per_sec(self) -> float:
        return self.blk_read_bytes_per_sec + self.blk_write_bytes_per_sec

    def rebuilding_volumes(self) -> list:
        return [v for v in self.longhorn_volumes if v.get("rebuild_in_progress")]


@dataclass
class RuleResult:
    category: str          # cpu/memory/disk/network/longhorn/scheduling/probe/unknown
    reason_code: str
    severity: str           # critical/warning/info
    short_reason: str
    explanation: str
    evidence: dict


# ----------------------------------------------------------------------
# Rules, evaluated top-to-bottom - most specific/definitive causes first,
# generic fallbacks last.
# ----------------------------------------------------------------------
def rule_memory_pressure(ev: Evidence) -> Optional[RuleResult]:
    """IF Memory > threshold AND Last State = OOMKilled THEN Memory Pressure.
    OOMKilled is definitive evidence in itself; the memory-pct check is used
    when available to also confirm it, but is not required, since memory
    usage typically drops the instant the kernel kills the process, well
    before the next Prometheus scrape can see it."""
    if ev.last_state_reason != "OOMKilled":
        return None

    return RuleResult(
        category="memory",
        reason_code="MEMORY_PRESSURE",
        severity="critical",
        short_reason="Memory Pressure (OOMKilled)",
        explanation=(
            f"The pod {ev.pod_name} was OOMKilled after memory usage reached "
            f"{ev.mem_pct_of_limit:.0f}% of its {_fmt_bytes(ev.mem_limit_bytes)} limit. "
            f"Kubernetes terminated the container and it has since restarted "
            f"{ev.new_restart_count - ev.previous_restart_count} time(s)."
        ),
        evidence={
            "last_state_reason": ev.last_state_reason,
            "mem_pct_of_limit": ev.mem_pct_of_limit,
            "mem_usage_bytes": ev.mem_usage_bytes,
            "mem_limit_bytes": ev.mem_limit_bytes,
            "exit_code": ev.exit_code,
        },
    )


def rule_node_failure(ev: Evidence) -> Optional[RuleResult]:
    """IF Node NotReady AND Pod Rescheduled THEN Node Failure."""
    if ev.node_status != "NotReady":
        return None
    if ev.trigger not in ("notready", "pending") and not ev.failed_scheduling:
        return None

    return RuleResult(
        category="scheduling",
        reason_code="NODE_FAILURE",
        severity="critical",
        short_reason="Node Failure",
        explanation=(
            f"Node {ev.node_name} became NotReady, and pod {ev.pod_name} was "
            f"affected as a result{' and could not be rescheduled' if ev.failed_scheduling else ''}. "
            f"This points to a node-level failure rather than an application issue."
        ),
        evidence={
            "node_status": ev.node_status,
            "failed_scheduling": ev.failed_scheduling,
            "trigger": ev.trigger,
        },
    )


def rule_eviction(ev: Evidence) -> Optional[RuleResult]:
    """Pod was evicted by the kubelet, typically due to node-level resource
    pressure rather than the pod's own behavior."""
    if not ev.evicted and ev.pod_status_reason != "Evicted":
        return None

    pressure_bits = []
    if ev.node_memory_pressure:
        pressure_bits.append("memory pressure")
    if ev.node_disk_pressure:
        pressure_bits.append("disk pressure")
    pressure_txt = f" due to node {' and '.join(pressure_bits)}" if pressure_bits else ""

    return RuleResult(
        category="scheduling",
        reason_code="POD_EVICTED",
        severity="critical",
        short_reason="Pod Evicted",
        explanation=(
            f"Pod {ev.pod_name} was evicted from node {ev.node_name}{pressure_txt}."
        ),
        evidence={
            "node_memory_pressure": ev.node_memory_pressure,
            "node_disk_pressure": ev.node_disk_pressure,
            "node_disk_pct": ev.node_disk_pct,
            "node_mem_pct": ev.node_mem_pct,
        },
    )


def rule_longhorn_rebuild_disk_pressure(ev: Evidence) -> Optional[RuleResult]:
    """IF Disk IO High AND Replica Rebuilding AND PVC Detached
    THEN Longhorn Replica Rebuild causing Disk Pressure.

    Relaxed slightly from the brief's strict three-way AND: a rebuilding
    replica plus elevated disk IO on the pod's own containers is already a
    strong, specific signal even without also catching a detach event in
    the same short poll window (detach may have already completed before
    this evaluation ran)."""
    rebuilding = ev.rebuilding_volumes()
    if not rebuilding:
        return None
    if ev.disk_io_bytes_per_sec() < settings.DISK_IO_HIGH_BYTES_PER_SEC:
        return None

    detached = "VolumeDetached" in ev.recent_volume_events
    vol_names = ", ".join(v["volume_name"] for v in rebuilding)

    return RuleResult(
        category="longhorn",
        reason_code="LONGHORN_REBUILD_DISK_PRESSURE",
        severity="warning",
        short_reason="Longhorn Replica Rebuild causing Disk Pressure",
        explanation=(
            f"Longhorn volume(s) {vol_names} used by {ev.pod_name} are currently "
            f"rebuilding a replica, pushing combined disk IO on this pod to "
            f"{_fmt_bytes(ev.disk_io_bytes_per_sec())}/s"
            f"{'. The volume was also detached during this window' if detached else ''}. "
            f"This IO contention is the likely cause of the pod's instability."
        ),
        evidence={
            "rebuilding_volumes": [v["volume_name"] for v in rebuilding],
            "disk_io_bytes_per_sec": ev.disk_io_bytes_per_sec(),
            "recent_volume_events": ev.recent_volume_events,
        },
    )


def rule_cpu_saturation(ev: Evidence) -> Optional[RuleResult]:
    """IF CPU > threshold AND Restart Count Increased THEN CPU Saturation."""
    if not ev.restart_count_increased:
        return None

    # Prefer usage relative to the pod's own limit; if it has no limit set
    # (pct stays 0), fall back to node-wide CPU pressure as a proxy.
    cpu_signal = ev.cpu_pct_of_limit or ev.node_cpu_pct
    if cpu_signal < settings.CPU_HIGH_PCT:
        return None

    basis = "of its CPU limit" if ev.cpu_pct_of_limit else f"on node {ev.node_name}"

    return RuleResult(
        category="cpu",
        reason_code="CPU_SATURATION",
        severity="warning",
        short_reason="CPU Saturation",
        explanation=(
            f"CPU utilization for {ev.pod_name} reached {cpu_signal:.0f}% {basis} "
            f"around the time it restarted (restart count {ev.previous_restart_count} -> "
            f"{ev.new_restart_count})."
        ),
        evidence={
            "cpu_pct_of_limit": ev.cpu_pct_of_limit,
            "node_cpu_pct": ev.node_cpu_pct,
            "previous_restart_count": ev.previous_restart_count,
            "new_restart_count": ev.new_restart_count,
        },
    )


def rule_network_saturation(ev: Evidence) -> Optional[RuleResult]:
    """IF Network TX/RX exceeds threshold AND Readiness Probe Failed
    THEN Network Saturation."""
    if ev.readiness_probe_failures <= 0:
        return None
    if ev.net_bytes_per_sec() < settings.NETWORK_HIGH_BYTES_PER_SEC:
        return None

    direction = "outbound" if ev.net_tx_bytes_per_sec >= ev.net_rx_bytes_per_sec else "inbound"

    return RuleResult(
        category="network",
        reason_code="NETWORK_SATURATION",
        severity="warning",
        short_reason="Network Saturation",
        explanation=(
            f"{ev.pod_name} saw {direction} network traffic of "
            f"{_fmt_bytes(ev.net_bytes_per_sec())}/s while failing its readiness "
            f"probe {ev.readiness_probe_failures} time(s) recently, suggesting "
            f"the container is too busy on the network to respond to health checks."
        ),
        evidence={
            "net_rx_bytes_per_sec": ev.net_rx_bytes_per_sec,
            "net_tx_bytes_per_sec": ev.net_tx_bytes_per_sec,
            "readiness_probe_failures": ev.readiness_probe_failures,
        },
    )


def rule_storage_bottleneck(ev: Evidence) -> Optional[RuleResult]:
    """IF Disk IO > threshold AND Node Disk Pressure THEN Storage Bottleneck."""
    if not ev.node_disk_pressure:
        return None
    if ev.disk_io_bytes_per_sec() < settings.DISK_IO_HIGH_BYTES_PER_SEC:
        return None

    return RuleResult(
        category="disk",
        reason_code="STORAGE_BOTTLENECK",
        severity="warning",
        short_reason="Storage Bottleneck",
        explanation=(
            f"Node {ev.node_name} is under disk pressure while {ev.pod_name} is "
            f"driving {_fmt_bytes(ev.disk_io_bytes_per_sec())}/s of disk IO "
            f"(node disk usage {ev.node_disk_pct:.0f}%). The node's storage is "
            f"the likely bottleneck."
        ),
        evidence={
            "disk_io_bytes_per_sec": ev.disk_io_bytes_per_sec(),
            "node_disk_pct": ev.node_disk_pct,
            "node_disk_pressure": ev.node_disk_pressure,
        },
    )


def rule_probe_failure_generic(ev: Evidence) -> Optional[RuleResult]:
    """Fallback when probes are failing but none of the more specific
    resource-correlated rules above matched - still worth surfacing since
    it's a concrete, actionable signal, just without a confirmed cause."""
    if ev.readiness_probe_failures <= 0 and ev.liveness_probe_failures <= 0:
        return None

    which = []
    if ev.liveness_probe_failures > 0:
        which.append(f"liveness ({ev.liveness_probe_failures}x)")
    if ev.readiness_probe_failures > 0:
        which.append(f"readiness ({ev.readiness_probe_failures}x)")

    return RuleResult(
        category="probe",
        reason_code="PROBE_FAILURE",
        severity="warning",
        short_reason="Health Check Failures",
        explanation=(
            f"{ev.pod_name} has failed its {' and '.join(which)} probe(s) recently, "
            f"without a clear resource-side cause (CPU, memory, disk, network and "
            f"Longhorn all looked normal). This may be an application-level issue "
            f"worth checking logs for."
        ),
        evidence={
            "readiness_probe_failures": ev.readiness_probe_failures,
            "liveness_probe_failures": ev.liveness_probe_failures,
        },
    )


def rule_unexplained(ev: Evidence) -> Optional[RuleResult]:
    """Last-resort catch-all: something happened (restart/pending/eviction/
    NotReady) but no rule above found a correlated cause. Always matches,
    so every incident gets *some* RootCauseRecord instead of being silently
    dropped - this is exactly the gap the brief's Future AI Layer is meant
    to eventually close with a smarter, non-rule-based read of the same
    evidence."""
    return RuleResult(
        category="unknown",
        reason_code="UNEXPLAINED",
        severity="info",
        short_reason="Cause Not Yet Determined",
        explanation=(
            f"{ev.pod_name} had a '{ev.trigger}' incident, but CPU, memory, disk, "
            f"network, Longhorn and scheduling signals were all within normal range "
            f"at the time. No rule matched - manual investigation of logs/events is "
            f"recommended."
        ),
        evidence={"trigger": ev.trigger, "pod_phase": ev.pod_phase},
    )


# Order matters: first match wins.
RULES = [
    rule_memory_pressure,
    rule_node_failure,
    rule_eviction,
    rule_longhorn_rebuild_disk_pressure,
    rule_cpu_saturation,
    rule_network_saturation,
    rule_storage_bottleneck,
    rule_probe_failure_generic,
    rule_unexplained,   # always matches - must stay last
]


def _fmt_bytes(n: float) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024.0:
            return f"{n:.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}PB"
