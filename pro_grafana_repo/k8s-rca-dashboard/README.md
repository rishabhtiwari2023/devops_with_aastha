# K3s Pod Failure Detection & RCA Dashboard

A FastAPI-based dashboard for Kubernetes pod failure detection, root cause analysis, and cluster health monitoring.

## What this project includes

- Backend: FastAPI
- SQLite database for historical metrics and RCA records
- Kubernetes, Docker, Prometheus, and Longhorn collectors
- Rule-based RCA engine
- REST APIs and WebSocket push updates
- Jinja2-driven dashboard UI with dark theme
- Resource rankings, timeline, alerts, and cluster tree view

## Quick start

1. Copy `.env.example` to `.env` and update the configuration values.
2. Create a Python virtual environment:
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```
3. Install dependencies:
   ```powershell
   pip install -r requirements.txt
   ```
4. Run the server:
   ```powershell
   python main.py
   ```
5. Open the dashboard in your browser:
   ```text
   http://127.0.0.1:8000/
   ```

## Configuration

Rename `.env.example` to `.env` and set:

- `RCA_DB_PATH` — path to the SQLite file
- `RCA_HOST` / `RCA_PORT` — server bind address
- `RCA_KUBE_IN_CLUSTER` — whether to use in-cluster Kubernetes config
- `RCA_KUBECONFIG_PATH` — kubeconfig path when not in-cluster
- `RCA_PROMETHEUS_URL` — Prometheus base URL
- `RCA_LONGHORN_API_URL` — Longhorn manager REST endpoint

## Available scripts

- `python main.py` — start FastAPI app
- `python -m py_compile main.py app/**/*.py` — syntax-check Python modules

## Project structure

- `main.py` — application entry point
- `app/core` — config and database setup
- `app/collectors` — metric collectors
- `app/rca` — rule-based root cause analysis engine
- `app/routers` — REST API endpoints
- `app/templates` — Jinja2 UI shell
- `app/static` — CSS and client-side JS

## Notes

- The dashboard is designed for clusters with frequent polling and real-time updates.
- The UI currently supports tree view, active alerts, timeline and resource rankings.
- Charts and filters can be extended by wiring ECharts into the frontend.
