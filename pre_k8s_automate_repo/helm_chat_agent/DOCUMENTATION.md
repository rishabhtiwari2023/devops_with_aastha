# ⛵ Helm Chart Generator Agent — Documentation

## Table of Contents
1. [Overview](#overview)
2. [What This App Does](#what-this-app-does)
3. [Key Features](#key-features)
4. [How It Works](#how-it-works)
5. [Architecture & Technical Details](#architecture--technical-details)
6. [How to Configure](#how-to-configure)
7. [User Guide & Examples](#user-guide--examples)
8. [Generated Helm Chart Structure](#generated-helm-chart-structure)
9. [Why This Matters](#why-this-matters)
10. [Summary](#summary)

---

## Overview

Helm Chart Generator Agent is an **AI-powered DevOps automation tool** that automatically generates production-ready Helm charts for any microservice. Using LangGraph for workflow orchestration and Gemini 1.5 Flash for analysis and generation, it scans a microservice folder, extracts service metadata, and writes a complete Helm chart with all necessary Kubernetes manifests.

Designed for developers who want to automate Kubernetes deployment without manually writing YAML, it demonstrates agentic AI applied to infrastructure-as-code generation.

---

## What This App Does

Helm Chart Generator Agent provides complete Helm chart automation by:

- **Microservice Scanning**: Reads `.env`, `README.md`, `Dockerfile`, `requirements.txt`/`package.json`
- **Metadata Extraction**: Uses Gemini LLM to extract ports, image names, environment variables, and service type
- **Helm Chart Generation**: Generates all required Helm chart files (Chart.yaml, values.yaml, templates)
- **Conditional Manifests**: Creates ConfigMap for non-secret env vars, Secret for secrets, Ingress if enabled, HPA if enabled
- **File Writing**: Writes the complete `helm/<service-name>/` folder to the microservice directory
- **Validation**: Shows generated files and allows review before deployment

---

## Key Features

### 1. **LangGraph Workflow**
- 4-node agent workflow: Read → Analyze → Generate → Write
- State management across nodes
- Error handling and validation

### 2. **Intelligent Analysis**
- Detects service type (LoadBalancer, ClusterIP, NodePort)
- Identifies environment variables and classifies secrets
- Extracts container ports and health check endpoints
- Infers resource requirements from project structure

### 3. **Complete Helm Chart**
- `Chart.yaml` with version, description, and metadata
- `values.yaml` with configurable parameters
- `_helpers.tpl` with template functions
- `deployment.yaml` with replicas, resources, liveness/readiness probes
- `service.yaml` with service type and ports
- `configmap.yaml` for non-secret environment variables
- `secret.yaml` for sensitive environment variables
- `ingress.yaml` for HTTP routing (if enabled)
- `hpa.yaml` for horizontal pod autoscaling (if enabled)

### 4. **Streamlit UI**
- Microservice path selection
- Configuration options (ingress, HPA, resource limits)
- Real-time workflow progress display
- Generated file preview
- Download and copy functionality

---

## How It Works

### Workflow Diagram

```
User Input (Microservice Path)
        │
        ▼
┌────────────────────────────────────────────────────┐
│              LangGraph StateGraph                   │
│                                                    │
│  [read_microservice] ──→ scanned_files             │
│         │                                          │
│         ▼                                          │
│  [analyze_metadata] ──→ service_metadata           │
│         │                                          │
│         ▼                                          │
│  [generate_helm] ──→ helm_files                    │
│         │                                          │
│         ▼                                          │
│  [write_chart] ──→ written_path                    │
│         │                                          │
│        END                                         │
└────────────────────────────────────────────────────┘
        │
        ▼
Streamlit UI (Success + File Preview)
```

### State Schema

```python
class AgentState(TypedDict):
    microservice_path: str
    scanned_files: dict[str, str]
    service_metadata: dict
    helm_files: dict[str, str]
    written_path: str
    error: Optional[str]
```

---

## Architecture & Technical Details

### Technology Stack

| Component | Technology | Purpose |
|---|---|---|
| Workflow Orchestration | LangGraph | Agent workflow with state management |
| LLM | Gemini 1.5 Flash | Metadata extraction and Helm generation |
| LLM Abstraction | LangChain | Prompt engineering and response parsing |
| UI | Streamlit | Interactive web interface |
| File Operations | Python stdlib | Reading and writing Helm chart files |

### Key Design Decisions

| Decision | Why |
|---|---|
| LangGraph StateGraph | Supports multi-step workflows with state passing |
| Gemini 1.5 Flash | Fast, cost-effective, excellent code generation |
| File-based analysis | No need for running containers or complex parsing |
| Conditional manifest generation | Only create Ingress/HPA if user enables them |
| Write to local filesystem | Direct integration with existing project structure |

---

## How to Configure

### Environment Setup

```bash
# Install dependencies
pip install -r requirements.txt

# Configure API key
cp .env.example .env
# Add your GOOGLE_API_KEY to .env

# Run the app
streamlit run app.py
```

### Configuration Options

**`.env` file:**
```
GOOGLE_API_KEY=your_gemini_api_key_here
```

**Streamlit UI Options:**
- **Ingress Enabled**: Toggle to generate Ingress manifest
- **HPA Enabled**: Toggle to generate Horizontal Pod Autoscaler
- **Replicas**: Default replica count (default: 1)
- **Resource Limits**: CPU/memory limits (optional)

---

## User Guide & Examples

### Example 1: Generate Helm Chart for Python Flask App

1. **Input**: Path to Flask microservice folder
2. **Files Detected**:
   - `app.py` (Flask application)
   - `requirements.txt` (dependencies)
   - `.env` (environment variables)
   - `Dockerfile` (container definition)
3. **Analysis Result**:
   - Service type: LoadBalancer
   - Port: 5000
   - Image: my-flask-app:latest
   - Env vars: DATABASE_URL, API_KEY (secret)
4. **Generated Chart**:
   - `helm/my-flask-app/` with all manifests
5. **Deploy**:
   ```bash
   helm install my-flask-app ./helm/my-flask-app
   ```

### Example 2: Generate Helm Chart for Node.js Express App

1. **Input**: Path to Express microservice folder
2. **Files Detected**:
   - `server.js` (Express application)
   - `package.json` (dependencies)
   - `.env` (environment variables)
3. **Analysis Result**:
   - Service type: ClusterIP
   - Port: 3000
   - Image: my-express-app:latest
   - Env vars: MONGO_URI, JWT_SECRET (secret)
4. **Generated Chart**:
   - `helm/my-express-app/` with ConfigMap for non-secret vars
5. **Deploy**:
   ```bash
   helm install my-express-app ./helm/my-express-app
   ```

---

## Generated Helm Chart Structure

```
helm/<service-name>/
├── Chart.yaml
│   ├── apiVersion: v2
│   ├── name: <service-name>
│   ├── version: 0.1.0
│   └── description: Auto-generated Helm chart
│
├── values.yaml
│   ├── replicaCount: 1
│   ├── image: <image-name>
│   ├── service: {type, port}
│   ├── resources: {limits, requests}
│   ├── ingress: {enabled, className, hosts}
│   └── autoscaling: {enabled, minReplicas, maxReplicas}
│
├── .helmignore
│
└── templates/
    ├── _helpers.tpl
    │   └── Template functions for labels, names
    │
    ├── deployment.yaml
    │   ├── ReplicaSet
    │   ├── Pod template
    │   ├── Containers
    │   ├── Resources
    │   └── Liveness/Readiness probes
    │
    ├── service.yaml
    │   ├── Service type (LoadBalancer/ClusterIP/NodePort)
    │   └── Port mappings
    │
    ├── configmap.yaml (if non-secret env vars)
    │   └── Environment variable key-value pairs
    │
    ├── secret.yaml (if secrets detected)
    │   └── Base64-encoded secret values
    │
    ├── ingress.yaml (if ingress enabled)
    │   ├── Ingress rules
    │   └── Host and path configuration
    │
    └── hpa.yaml (if HPA enabled)
        ├── HorizontalPodAutoscaler
        └── CPU/memory target utilization
```

---

## Why This Matters

### For Developers

- **Time Savings**: Eliminates manual YAML writing for each microservice
- **Consistency**: Standardized Helm chart structure across all services
- **Best Practices**: Generated charts follow Kubernetes and Helm best practices
- **Learning**: See how Helm charts are structured by examining generated files

### For DevOps Teams

- **Automation**: Integrate into CI/CD pipelines for automatic chart generation
- **Scalability**: Generate charts for dozens of microservices quickly
- **Standardization**: Enforce organizational standards via templates
- **Error Reduction**: LLM-based analysis reduces human error in manual YAML writing

### For Organizations

- **Faster Onboarding**: New developers can deploy services without deep Helm knowledge
- **Infrastructure as Code**: All deployment manifests are version-controlled
- **GitOps Ready**: Generated charts can be committed to Git for GitOps workflows
- **Cost Efficiency**: Reduces DevOps engineering time for chart maintenance

---

## Summary

Helm Chart Generator Agent is an **AI-powered DevOps automation tool** that generates production-ready Helm charts for any microservice. Using LangGraph for workflow orchestration and Gemini 1.5 Flash for analysis, it scans microservice files, extracts metadata, and writes complete Kubernetes manifests.

**Key Takeaways:**
- 4-node LangGraph workflow: Read → Analyze → Generate → Write
- Intelligent metadata extraction using Gemini LLM
- Generates complete Helm chart with all necessary manifests
- Conditional generation of ConfigMap, Secret, Ingress, HPA
- Streamlit UI for easy configuration and preview

**Technology Stack:**
- LangGraph for workflow orchestration
- LangChain for LLM abstraction
- Gemini 1.5 Flash for analysis and generation
- Streamlit for UI
- Python stdlib for file operations

**Next Steps:**
1. Configure `GOOGLE_API_KEY` in `.env`
2. Run `streamlit run app.py`
3. Select microservice path and configure options
4. Click "Generate Helm Chart"
5. Review generated files in `helm/<service-name>/`
6. Deploy with `helm install <service-name> ./helm/<service-name>`
