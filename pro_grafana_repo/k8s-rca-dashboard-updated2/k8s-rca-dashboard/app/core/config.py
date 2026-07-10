"""
Central configuration for the K3s Pod Failure Detection & RCA Dashboard.

All tunable values (poll intervals, thresholds used by the rule engine,
connection endpoints, etc.) live here so the rest of the codebase never
hardcodes a number. Values can be overridden via environment variables
or a `.env` file placed next to this project's root.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent.parent  # project root


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="RCA_", extra="ignore")

    # ---------------------------------------------------------------
    # General
    # ---------------------------------------------------------------
    APP_NAME: str = "K3s Pod Failure & RCA Dashboard"
    ENV: str = "production"
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # ---------------------------------------------------------------
    # Database
    # ---------------------------------------------------------------
    DB_PATH: str = str(BASE_DIR / "data" / "rca.db")

    # ---------------------------------------------------------------
    # Kubernetes
    # ---------------------------------------------------------------
    # If running inside the cluster, leave KUBE_IN_CLUSTER=True.
    # If running from a workstation, point KUBECONFIG_PATH at the kubeconfig.
    KUBE_IN_CLUSTER: bool = False
    KUBECONFIG_PATH: str = str(Path.home() / ".kube" / "config")

    # ---------------------------------------------------------------
    # Prometheus
    # ---------------------------------------------------------------
    # Leave blank to disable Prometheus collection until you have
    # forwarded a real Prometheus endpoint to localhost:9090.
    PROMETHEUS_URL: str = ""
    PROM_NODE_LABEL: str = "node"
    PROM_INSTANCE_TO_NODE: dict[str, str] = {}

    # ---------------------------------------------------------------
    # Docker
    # ---------------------------------------------------------------
    # One Docker daemon per node. For local Windows environments, leave
    # this empty unless you have a reachable Docker host mapping.
    DOCKER_HOSTS: dict[str, str] = {}

    # ---------------------------------------------------------------
    # Longhorn
    # ---------------------------------------------------------------
    # Leave blank if Longhorn is not installed or not port-forwarded.
    LONGHORN_API_URL: str = ""
    # Optional: longhorn-manager's Prometheus-format metrics endpoint, used only
    # to enrich volumes with read/write IOPS, throughput and latency (the /v1
    # REST objects above don't carry these). Leave blank to skip - those fields
    # simply stay at 0. e.g. "http://longhorn-backend.longhorn-system.svc.cluster.local:9500/metrics"
    LONGHORN_METRICS_URL: str = ""


    # ---------------------------------------------------------------
    # Polling / collection cadence (seconds)
    # ---------------------------------------------------------------
    K8S_POLL_INTERVAL: int = 5
    DOCKER_POLL_INTERVAL: int = 5
    PROM_POLL_INTERVAL: int = 10
    LONGHORN_POLL_INTERVAL: int = 10
    RCA_EVAL_INTERVAL: int = 5

    # ---------------------------------------------------------------
    # Historical retention
    # ---------------------------------------------------------------
    METRICS_RETENTION_HOURS: int = 168  # 7 days
    EVENTS_RETENTION_HOURS: int = 720   # 30 days

    # ---------------------------------------------------------------
    # Rule-Based RCA Engine thresholds
    # ---------------------------------------------------------------
    CPU_HIGH_PCT: float = 95.0
    MEM_HIGH_PCT: float = 95.0
    DISK_IO_HIGH_BYTES_PER_SEC: float = 100 * 1024 * 1024   # 100 MB/s
    NETWORK_HIGH_BYTES_PER_SEC: float = 50 * 1024 * 1024    # 50 MB/s
    NODE_DISK_PRESSURE_PCT: float = 90.0
    RESTART_WINDOW_MINUTES: int = 10        # window to consider "restart count increased"
    PROBE_FAILURE_LOOKBACK_MINUTES: int = 5
    # How long to wait before re-raising an RCA alert for the same pod+condition
    # when the trigger isn't a discrete restart event (e.g. stuck Pending,
    # NotReady, Evicted) - prevents re-alerting every RCA_EVAL_INTERVAL seconds
    # for a condition that just hasn't resolved yet.
    ALERT_DEDUP_COOLDOWN_MINUTES: int = 10

    # ---------------------------------------------------------------
    # WebSocket broadcast
    # ---------------------------------------------------------------
    WS_BROADCAST_INTERVAL: float = 3.0


settings = Settings()

# Global in-memory cache to map Node IP addresses to Node Names (filled by k8s collector)
NODE_IP_TO_NAME: dict[str, str] = {}

