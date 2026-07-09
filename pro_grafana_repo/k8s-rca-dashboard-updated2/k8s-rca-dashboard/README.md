# K3s Pod Failure Detection & RCA Dashboard

A FastAPI-based monitoring and troubleshooting dashboard for Kubernetes clusters. This project collects cluster state from Kubernetes, Prometheus, Docker, and Longhorn, stores history in SQLite, and performs rule-based root cause analysis (RCA) for pod incidents.

## Overview

This repository is designed as a modular observability system for K3s and similar Kubernetes environments. It is not a full replacement for Grafana/Prometheus, but rather a compact dashboard that:

- detects pod failures and restart events
- correlates runtime, node, and storage signals
- explains incidents using deterministic RCA rules
- pushes alerts and summaries to a browser UI in real time

The system is built on FastAPI with:

- background collectors for external APIs
- an SQLite-backed event and metric store
- a pure Python rule engine for RCA
- REST endpoints for list/detail views
- a WebSocket endpoint for live alert push notifications
- a Jinja2 frontend shell serving static assets

## Repository layout and code overview

### `main.py`

The application entry point.

- creates SQLite tables at startup
- starts background collector services and the RCA engine via `app.background.services`
- mounts REST routers and the `/ws` WebSocket endpoint
- serves the Jinja2 dashboard HTML at `/`
- exposes a simple healthcheck at `/healthz`
- supports development reload when `ENV != 'production'`

### `app/core/config.py`

Loads runtime settings from environment variables or `.env` using `pydantic-settings`.

Key configuration groups:

- general app settings: `APP_NAME`, `ENV`, `HOST`, `PORT`
- database path: `DB_PATH`
- Kubernetes client mode: `KUBE_IN_CLUSTER`, `KUBECONFIG_PATH`
- Prometheus connectivity: `PROMETHEUS_URL`, `PROM_NODE_LABEL`, `PROM_INSTANCE_TO_NODE`
- Docker node hosts: `DOCKER_HOSTS`
- Longhorn API endpoints: `LONGHORN_API_URL`, `LONGHORN_METRICS_URL`
- polling cadence and retention limits
- RCA thresholds for CPU, memory, disk, network, and probe failures

### `app/core/database.py`

Defines the SQLAlchemy engine and session factory.

- uses SQLite with `check_same_thread=False`
- enables WAL mode and foreign keys
- exposes `SessionLocal` and `get_db()` for request dependencies
- imports model metadata and creates all tables at startup

### `app/background.py`

Orchestrates all background services.

- starts Kubernetes, Docker, Prometheus, and Longhorn collectors conditionally
- starts the RCA engine loop
- schedules hourly retention cleanup via APScheduler
- stops all running tasks cleanly on shutdown
- wires an `on_alert` callback so new alerts are broadcast via WebSockets

### `app/collectors`

Data collectors poll external systems and populate SQLite.

- `k8s_collector.py`
  - polls Kubernetes Nodes, Pods, and Events
  - upserts node state and pod state tables
  - tracks restart count changes in `RestartHistory`
  - calculates liveness/readiness failure counts from recent events
  - resolves pod ownership (Deployment/StatefulSet/DaemonSet/ReplicaSet)

- `docker_collector.py`
  - connects to each configured Docker daemon via `DOCKER_HOSTS`
  - reads container stats to capture network and block IO
  - maps containers back to pods using Kubernetes labels
  - computes per-second rates from cumulative Docker counters

- `prometheus_collector.py`
  - runs PromQL instant queries against Prometheus
  - collects pod CPU/memory usage and resource requests/limits
  - collects node CPU, memory, disk, load, and pressure conditions
  - stores results in `PodMetric` and `NodeMetric`

- `longhorn_collector.py`
  - reads Longhorn volumes, replicas, and engines from the Longhorn API
  - stores volume state, robustness, replica details, and rebuild progress
  - synthesizes timeline events for volume state transitions
  - links Longhorn volumes back to pods via PVC names
  - optionally enriches Longhorn metrics via `LONGHORN_METRICS_URL`

- client helpers:
  - `k8s_client.py` — Kubernetes API client factory, supports in-cluster or kubeconfig mode
  - `docker_client.py` — Docker client factory for multiple node hosts
  - `prometheus_client.py` — async Prometheus instant query wrapper
  - `longhorn_client.py` — async Longhorn Manager REST wrapper

### `app/rca`

Root cause analysis engine and rules.

- `engine.py`
  - wakes every `RCA_EVAL_INTERVAL` seconds
  - discovers triggers from restart history, stuck pods, and NotReady node conditions
  - gathers evidence from pods, nodes, metrics, Docker, Longhorn, and recent events
  - evaluates rules top-to-bottom and persists the first matching verdict
  - writes `RootCauseRecord` and creates/or broadcasts alerts

- `rules.py`
  - defines deterministic RCA rule functions over an `Evidence` snapshot
  - includes rules for memory pressure, CPU saturation, network issues, disk pressure, scheduling failures, Longhorn rebuilds, and evictions
  - defines a catch-all fallback rule
  - produces human-readable `explanation` text and structured `evidence`

### `app/models`

ORM model definitions for all persisted data.

- `node.py` — current node state and status
- `pod.py` — current pod state, pod metadata, owner references, PVCs, Longhorn volume links, and container statuses
- `metrics.py` — append-only time series for pod, node, and Docker metrics
- `events.py` — Kubernetes events and restart history rows
- `longhorn.py` — Longhorn volume metric time series
- `alerts.py` — alert records surfaced in the UI
- `rca.py` — recorded root cause verdicts, explanation text, and evidence payload

### `app/routers`

REST API endpoints used by the frontend.

- `cluster.py` — cluster summary and cluster tree view
- `nodes.py` — node list, comparison, detail, metrics, and pod lists
- `pods.py` — pod list, detail, metrics history, docker history, restart history, and events
- `longhorn.py` — Longhorn volumes, summaries, volume detail, history, and associated pods
- `events.py` — Kubernetes events, restart history, and unified timeline
- `rca.py` — RCA record listing, summaries, recent verdicts, and detail
- `alerts.py` — active alerts, alert history, counts, acknowledge, and resolve
- `rankings.py` — ranked lists for CPU, memory, network, disk, IOPS, and restarts
- `ws.py` — WebSocket endpoint for live push notifications

### `app/utils`

Shared helper logic.

- `aggregation.py` — query helpers to fetch the latest row per pod/node and metric history for charts
- `serializers.py` — generic SQLAlchemy row-to-dict serializers used by routers
- `retention.py` — hourly cleanup job for old metrics, events, RCA records, and alerts
- `websocket_manager.py` — singleton WebSocket broadcaster for live updates

### `app/templates` and `app/static`

- `app/templates/index.html` — dashboard shell served by FastAPI
- `app/static/css/dashboard.css` — dashboard styling
- `app/static/js/dashboard.js` — client-side UI logic and WebSocket handling

## Deployment and startup

1. Copy `.env.example` to `.env` and update settings.
2. Create a Python virtual environment:
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```
3. Install dependencies:
   ```powershell
   pip install -r requirements.txt
   ```
4. Start the server:
   ```powershell
   python main.py
   ```
5. Open the dashboard at:
   ```text
   http://127.0.0.1:8000/
   ```

## Configuration details

Rename `.env.example` to `.env` and configure these values:

- `RCA_DB_PATH` — path to the SQLite database file, e.g. `./data/rca.db`
- `RCA_HOST` — bind address for the server
- `RCA_PORT` — HTTP port for the server
- `RCA_KUBE_IN_CLUSTER` — set to `true` when the dashboard runs inside Kubernetes
- `RCA_KUBECONFIG_PATH` — kubeconfig path for local execution
- `RCA_PROMETHEUS_URL` — Prometheus base URL for metrics scraping
- `RCA_LONGHORN_API_URL` — Longhorn manager REST endpoint
- `RCA_LONGHORN_METRICS_URL` — optional Longhorn metrics endpoint for IOPS/latency
- `RCA_DOCKER_HOSTS` — map of node names to Docker engine URLs for Docker collection

Additional runtime settings are defined in `app/core/config.py`.

## How the code works

### Background collection flow

- `app/background.py` starts all enabled collectors and the RCA engine.
- `KubernetesCollector` polls nodes, pods, and events every `K8S_POLL_INTERVAL` seconds.
- `PrometheusCollector` polls Prometheus every `PROM_POLL_INTERVAL` seconds.
- `DockerCollector` polls Docker hosts every `DOCKER_POLL_INTERVAL` seconds.
- `LonghornCollector` polls Longhorn every `LONGHORN_POLL_INTERVAL` seconds.
- The hourly retention job deletes old time-series and event rows.

### RCA flow

- `app/rca/engine.py` discovers triggers from restart records, pod status, and node conditions.
- It collects evidence from all source tables and recent events.
- `app/rca/rules.py` evaluates the evidence against an ordered rule set.
- First matching rule produces a root cause verdict written to `RootCauseRecord`.
- Alerts are created and broadcast to connected WebSocket clients.

### API and frontend flow

- Frontend requests data from `/api/*` endpoints
- Alerts and RCA updates arrive on `/ws`
- Routers use `app/utils/serializers.py` to convert SQLAlchemy rows to JSON-friendly dictionaries
- Ranking endpoints return full sorted pod lists, not just top results

## Useful commands

- `python main.py` — start the FastAPI server
- `python -m py_compile main.py app/**/*.py` — syntax-check the code
- `python -m uvicorn main:app --reload` — run with Uvicorn reload for development

## Troubleshooting

- verify Prometheus and Longhorn URLs are reachable from the host
- confirm Kubernetes access via `KUBECONFIG_PATH` or in-cluster service account
- inspect `logs/` for startup and collector errors
- check that `app/core/database.py` created `data/rca.db`

## Notes

- The dashboard is built for frequent polling and real-time updates.
- The collector loops are resilient: if one source fails, the others continue.
- The rule engine is deterministic and intentionally AI-free.
- This project is a modular base for adding new collectors, RCA rules, and UI panels.
kubectl port-forward -n monitoring svc/simple-prometheus 9090:9090
Forwarding from 127.0.0.1:9090 -> 9090
Forwarding from [::1]:9090 -> 9090

