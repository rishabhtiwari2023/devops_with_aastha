# ⛵ Helm Chart Generator Agent

An AI-powered agent that **automatically generates production-ready Helm charts** for any microservice — just point it at your microservice folder.

## How It Works

```
Microservice Path (Streamlit UI)
        │
        ▼
[Node 1: Read]  ──► Scans .env, README.md, Dockerfile, requirements.txt/package.json
        │
        ▼
[Node 2: Analyze]  ──► Gemini LLM extracts service metadata (ports, image, env vars, etc.)
        │
        ▼
[Node 3: Generate]  ──► Gemini LLM generates all Helm chart files
        │
        ▼
[Node 4: Write]  ──► Writes helm/<service-name>/ folder to your microservice directory
```

## Generated Structure

```
helm/<service>/
├── Chart.yaml
├── values.yaml
├── .helmignore
└── templates/
    ├── _helpers.tpl
    ├── deployment.yaml
    ├── service.yaml
    ├── configmap.yaml   (if non-secret env vars detected)
    ├── secret.yaml      (if secrets detected)
    ├── ingress.yaml     (if ingress enabled)
    └── hpa.yaml         (if HPA enabled)
```

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# Add your GOOGLE_API_KEY to .env
streamlit run app.py
```

## Tech Stack

- **LangGraph** — agent workflow orchestration
- **LangChain** — LLM abstraction
- **Gemini 1.5 Flash** — LLM for analysis and generation
- **Streamlit** — UI
