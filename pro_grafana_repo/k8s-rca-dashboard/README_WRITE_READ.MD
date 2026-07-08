# Raptor Mini

## Project Overview

This repository is a **FastAPI-based dashboard** for Kubernetes pod failure detection and Root Cause Analysis (RCA). It is designed to:

- Collect cluster state from multiple sources
- Store historical records in SQLite
- Generate deterministic RCA explanations
- **No AI or LLMs are used** for RCA generation

---

# High-Level Architecture

## `main.py`

Application entry point.

Responsibilities:

- Creates database tables
- Starts background collectors
- Starts RCA engine
- Registers REST API routers
- Registers WebSocket endpoint
- Serves frontend at `/`
- Provides `/healthz` endpoint for liveness checks

---

# Core Components

## `app/core`

### `config.py`

Loads runtime configuration from:

- `.env`
- Environment variables

### `database.py`

Responsible for:

- SQLite configuration
- Enabling WAL mode
- Database session helpers

---

# Background Services

## `app/background.py`

Responsible for:

- Starting all collectors
- Starting RCA engine
- Running hourly retention scheduler
- Broadcasting alerts over WebSocket whenever a new RCA record is created

---

# Data Collection Subsystems

## Kubernetes Collector

**File**

```
app/collectors/k8s_collector.py
```

Polls Kubernetes resources:

- Nodes
- Pods
- Events

Updates current state tables:

- Node
- Pod
- ContainerStatus
- K8sEvent
- RestartHistory

Features:

- Tracks restart count changes
- Resolves pod ownership
  - Deployment
  - StatefulSet
  - DaemonSet
  - ReplicaSet
- Updates probe failure counts from unhealthy events

---

## Prometheus Collector

**File**

```
app/collectors/prometheus_collector.py
```

Runs PromQL instant queries against:

```
RCA_PROMETHEUS_URL
```

Collects Pod metrics:

- CPU usage
- Memory usage
- CPU requests
- CPU limits
- Memory requests
- Memory limits

Collects Node metrics:

- CPU
- Memory
- Disk
- Load
- Pressure metrics

Stores snapshots into:

- PodMetric
- NodeMetric

---

## Docker Collector

**File**

```
app/collectors/docker_collector.py
```

Connects to Docker hosts configured in:

```
DOCKER_HOSTS
```

Collects:

- Network RX/TX
- Block IO Read
- Block IO Write
- IOPS

Maps containers to Kubernetes Pods using standard labels.

Calculates:

- Per-second network rates
- Per-second disk rates

Stores data in:

- DockerMetric

---

## Longhorn Collector

**File**

```
app/collectors/longhorn_collector.py
```

Reads Longhorn Manager API:

- Volumes
- Replicas
- Engines

Stores:

- LonghornVolumeMetric

Detects transitions such as:

- Degraded
- Faulted
- Rebuilding

Creates synthetic Kubernetes-like events.

Maps Longhorn volumes back to Pods using PVC names.

---

# RCA Engine

## `app/rca/engine.py`

Runs every:

```
RCA_EVAL_INTERVAL
```

Triggers RCA when:

- Restart history changes
- Pod becomes Pending
- Pod becomes Failed
- Pod becomes Evicted
- Pod runs on a NotReady node

Collects evidence from:

- Pod state
- Node state
- Prometheus metrics
- Docker metrics
- Longhorn metrics
- Recent Kubernetes events

Creates:

- RootCauseRecord
- Alert

Broadcasts alerts over WebSocket.

---

# RCA Rules

## `app/rca/rules.py`

Contains deterministic RCA rules implemented as pure functions.

Example rules:

- OOMKilled
- Memory pressure
- CPU saturation
- Node failure
- Node NotReady
- Pod eviction
- Longhorn rebuild
- Disk pressure
- Network saturation

Includes a fallback rule when no specific condition matches.

---

# Data Models

## Current State

- Node
- Pod
- ContainerStatus

---

## Time-Series Data

- PodMetric
- NodeMetric
- DockerMetric
- LonghornVolumeMetric

---

## Event History

- K8sEvent
- RestartHistory

---

## RCA

- RootCauseRecord
- Alert

---

# REST APIs

## Pod APIs

| Endpoint | Description |
|----------|-------------|
| `/api/pods` | List pods with latest metrics |
| `/api/pods/{uid}` | Pod details |
| `/api/pods/{uid}/metrics` | Pod metric history |
| `/api/pods/{uid}/docker` | Docker metric history |
| `/api/pods/{uid}/restarts` | Restart history |
| `/api/pods/{uid}/events` | Related Kubernetes events |

---

## Node APIs

| Endpoint | Description |
|----------|-------------|
| `/api/nodes` | List nodes |
| `/api/nodes/compare` | Node comparison |
| `/api/nodes/{name}` | Node details |
| `/api/nodes/{name}/metrics` | Metric history |
| `/api/nodes/{name}/pods` | Pods running on node |

---

## Cluster APIs

| Endpoint | Description |
|----------|-------------|
| `/api/cluster/summary` | Cluster KPIs |
| `/api/cluster/tree` | Hierarchical cluster tree |

---

## Events APIs

| Endpoint | Description |
|----------|-------------|
| `/api/events` | Kubernetes events |
| `/api/events/restarts` | Restart events |
| `/api/events/timeline` | Unified event timeline |

Timeline includes:

- Kubernetes Events
- Restart History
- RCA
- Longhorn Events

---

## RCA APIs

| Endpoint | Description |
|----------|-------------|
| `/api/rca` | RCA list |
| `/api/rca/recent` | Latest RCA per pod |
| `/api/rca/summary` | RCA summary |
| `/api/rca/pod/{uid}` | RCA for a pod |
| `/api/rca/{id}` | RCA details |

---

## Alerts APIs

| Endpoint | Description |
|----------|-------------|
| `/api/alerts` | Active alerts |
| `/api/alerts/history` | Alert history |
| `/api/alerts/counts` | Alert counts |
| `/api/alerts/{id}` | Alert detail |
| `/api/alerts/{id}/ack` | Acknowledge alert |
| `/api/alerts/{id}/resolve` | Resolve alert |

---

## Longhorn APIs

| Endpoint | Description |
|----------|-------------|
| `/api/longhorn/volumes` | List volumes |
| `/api/longhorn/volumes/summary` | Volume summary |
| `/api/longhorn/volumes/{volume_name}` | Volume details |
| `/api/longhorn/volumes/{volume_name}/history` | Volume history |
| `/api/longhorn/volumes/{volume_name}/pods` | Pods using volume |

---

## Rankings APIs

| Endpoint | Description |
|----------|-------------|
| `/api/rankings/cpu` | CPU ranking |
| `/api/rankings/memory` | Memory ranking |
| `/api/rankings/network` | Network ranking |
| `/api/rankings/disk` | Disk ranking |
| `/api/rankings/iops` | IOPS ranking |
| `/api/rankings/restarts` | Restart ranking |
| `/api/rankings/all` | Combined ranking |

---

# WebSocket

Endpoint:

```
/ws
```

Pushes live events:

- Alert notifications
- Periodic heartbeat (ping)

Managed by:

```
app/utils/websocket_manager.py
```

---

# Retention

**File**

```
app/utils/retention.py
```

Runs every hour.

Responsible for:

- Deleting expired metrics
- Deleting old events
- Deleting old history records
- Preventing SQLite database growth

---

# Configuration

Important environment variables:

```text
RCA_DB_PATH
RCA_HOST
RCA_PORT
RCA_KUBE_IN_CLUSTER
RCA_KUBECONFIG_PATH
RCA_PROMETHEUS_URL
RCA_LONGHORN_API_URL
RCA_LONGHORN_METRICS_URL
DOCKER_HOSTS
```

---

# Overall Workflow

The application performs the following workflow:

1. Collect cluster state from:
   - Kubernetes
   - Prometheus
   - Docker
   - Longhorn

2. Store current state and historical metrics in SQLite.

3. Detect:

   - Pod failures
   - Restarts
   - Resource anomalies
   - Infrastructure issues

4. Execute deterministic RCA rules.

5. Generate:

   - RootCauseRecord
   - Alerts

6. Expose information through:

   - REST APIs
   - WebSocket
   - Dashboard

7. Maintain historical timelines for troubleshooting.

---

# Summary

Raptor Mini is a lightweight Kubernetes monitoring and deterministic RCA platform that:

- Continuously monitors Kubernetes clusters
- Collects infrastructure metrics
- Tracks pod lifecycle and restart history
- Integrates Kubernetes, Prometheus, Docker, and Longhorn
- Generates rule-based Root Cause Analysis (without AI)
- Stores historical data in SQLite
- Exposes REST APIs, WebSocket notifications, and dashboard visualizations for troubleshooting and monitoring