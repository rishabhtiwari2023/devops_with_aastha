# Installation Guide

## Prerequisites

- Python 3.11+
- Access to your K3s cluster kubeconfig or running inside the cluster
- Prometheus reachable from the backend process
- Docker API access for node containers
- Longhorn REST API access for storage metrics

## Setup

1. Clone the repository.
2. Create and activate a virtual environment:
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```
3. Install dependencies:
   ```powershell
   pip install -r requirements.txt
   ```
4. Copy the example environment file:
   ```powershell
   copy .env.example .env
   ```
5. Edit `.env` with your cluster and Prometheus settings.

## Run

```powershell
python main.py
```

Open `http://127.0.0.1:8000/` in your browser.

## Troubleshooting

- If the server cannot connect to Kubernetes, verify `RCA_KUBECONFIG_PATH`.
- If Prometheus metrics are missing, confirm `RCA_PROMETHEUS_URL` and network access.
- Use `python -m py_compile main.py app/**/*.py` to validate Python syntax.
