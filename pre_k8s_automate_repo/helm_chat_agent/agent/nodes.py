import os
import re
import json
import yaml
from pathlib import Path
from typing import Dict, Any

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, SystemMessage

from .state import HelmAgentState


def _read_file_safe(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception:
        return ""


def _build_tree(root: str, max_depth: int = 3) -> str:
    lines = []
    root_path = Path(root)
    for item in sorted(root_path.rglob("*")):
        depth = len(item.relative_to(root_path).parts) - 1
        if depth > max_depth:
            continue
        indent = "  " * depth
        lines.append(f"{indent}{item.name}{'/' if item.is_dir() else ''}")
    return "\n".join(lines[:80])


def node_read_microservice(state: HelmAgentState) -> HelmAgentState:
    path = state["microservice_path"]
    logs = state.get("logs", [])
    errors = state.get("errors", [])

    if not os.path.isdir(path):
        errors.append(f"Path does not exist or is not a directory: {path}")
        return {**state, "errors": errors, "status": "error"}

    service_name = Path(path).name.replace(" ", "-").lower()

    env_content = ""
    for fname in [".env", ".env.example", ".env.sample", "env.example"]:
        candidate = os.path.join(path, fname)
        if os.path.exists(candidate):
            env_content = _read_file_safe(candidate)
            logs.append(f"Read {fname}")
            break

    readme_content = ""
    for fname in ["README.md", "readme.md", "Readme.md", "README.txt"]:
        candidate = os.path.join(path, fname)
        if os.path.exists(candidate):
            readme_content = _read_file_safe(candidate)
            logs.append(f"Read {fname}")
            break

    dockerfile_content = ""
    for fname in ["Dockerfile", "dockerfile", "Dockerfile.prod"]:
        candidate = os.path.join(path, fname)
        if os.path.exists(candidate):
            dockerfile_content = _read_file_safe(candidate)
            logs.append(f"Read {fname}")
            break

    dep_content = ""
    for fname in ["requirements.txt", "package.json", "pom.xml", "build.gradle", "go.mod", "pyproject.toml"]:
        candidate = os.path.join(path, fname)
        if os.path.exists(candidate):
            dep_content = _read_file_safe(candidate)
            logs.append(f"Read {fname} (dependency file)")
            break

    tree = _build_tree(path)
    logs.append("Built directory tree")

    return {
        **state,
        "service_name": service_name,
        "env_content": env_content,
        "readme_content": readme_content,
        "dockerfile_content": dockerfile_content,
        "dependency_file_content": dep_content,
        "directory_tree": tree,
        "logs": logs,
        "errors": errors,
        "status": "reading_done",
    }


def node_analyze_microservice(state: HelmAgentState) -> HelmAgentState:
    logs = state.get("logs", [])
    errors = state.get("errors", [])

    api_key = os.getenv("GOOGLE_API_KEY", "")
    if not api_key:
        errors.append("GOOGLE_API_KEY not set in environment.")
        return {**state, "errors": errors, "status": "error"}

    llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", google_api_key=api_key, temperature=0.1)

    system_prompt = """You are a Kubernetes and Helm chart expert. 
Analyze the provided microservice context and extract structured metadata.
Always respond with ONLY valid JSON — no markdown fences, no explanation."""

    user_prompt = f"""Analyze the following microservice and extract its Helm chart metadata.

SERVICE NAME: {state["service_name"]}

=== .env / .env.example ===
{state["env_content"] or "(not found)"}

=== README ===
{state["readme_content"][:3000] or "(not found)"}

=== Dockerfile ===
{state["dockerfile_content"] or "(not found)"}

=== Dependency File ===
{state["dependency_file_content"][:1000] or "(not found)"}

=== Directory Tree ===
{state["directory_tree"]}

Return a single JSON object with these keys:
{{
  "service_name": "kebab-case name",
  "image_repository": "inferred docker image repo, e.g. myorg/service-name",
  "image_tag": "latest",
  "container_port": 8080,
  "service_type": "ClusterIP",
  "service_port": 80,
  "replicas": 1,
  "cpu_request": "100m",
  "cpu_limit": "500m",
  "memory_request": "128Mi",
  "memory_limit": "512Mi",
  "env_vars": [{{"name": "KEY", "value": "VALUE"}}],
  "secrets": [{{"name": "SECRET_KEY", "description": "what it is"}}],
  "health_check_path": "/health",
  "ingress_enabled": false,
  "ingress_host": "service.example.com",
  "hpa_enabled": false,
  "min_replicas": 1,
  "max_replicas": 3,
  "description": "short one-line description of the service",
  "language": "python|node|java|go|other",
  "has_database": false,
  "config_map_keys": ["LIST_OF_NON_SECRET_ENV_KEYS"]
}}"""

    try:
        response = llm.invoke([SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)])
        raw = response.content.strip()
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
        metadata = json.loads(raw)
        logs.append("Analyzed microservice metadata via LLM")
    except Exception as e:
        errors.append(f"LLM analysis failed: {e}")
        metadata = {
            "service_name": state["service_name"],
            "image_repository": f"myorg/{state['service_name']}",
            "image_tag": "latest",
            "container_port": 8080,
            "service_type": "ClusterIP",
            "service_port": 80,
            "replicas": 1,
            "cpu_request": "100m",
            "cpu_limit": "500m",
            "memory_request": "128Mi",
            "memory_limit": "512Mi",
            "env_vars": [],
            "secrets": [],
            "health_check_path": "/health",
            "ingress_enabled": False,
            "ingress_host": f"{state['service_name']}.example.com",
            "hpa_enabled": False,
            "min_replicas": 1,
            "max_replicas": 3,
            "description": "Auto-generated Helm chart",
            "language": "other",
            "has_database": False,
            "config_map_keys": [],
        }
        logs.append("Used fallback metadata due to LLM error")

    return {**state, "service_metadata": metadata, "logs": logs, "errors": errors, "status": "analysis_done"}


def node_generate_helm_chart(state: HelmAgentState) -> HelmAgentState:
    logs = state.get("logs", [])
    errors = state.get("errors", [])
    meta = state["service_metadata"]

    api_key = os.getenv("GOOGLE_API_KEY", "")
    if not api_key:
        errors.append("GOOGLE_API_KEY not set.")
        return {**state, "errors": errors, "status": "error"}

    llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", google_api_key=api_key, temperature=0.1)

    system_prompt = """You are an expert Helm chart engineer. 
Generate complete, production-ready Helm chart files.
Respond ONLY with a JSON object where keys are relative file paths and values are file content strings.
No markdown, no explanation."""

    user_prompt = f"""Generate a complete Helm chart for the following microservice.

METADATA:
{json.dumps(meta, indent=2)}

README EXCERPT:
{state["readme_content"][:2000] or "(none)"}

ENV FILE:
{state["env_content"][:1500] or "(none)"}

Generate ALL of the following files:
1. Chart.yaml
2. values.yaml
3. .helmignore
4. templates/_helpers.tpl
5. templates/deployment.yaml
6. templates/service.yaml
7. templates/configmap.yaml  (only if there are non-secret env vars)
8. templates/secret.yaml     (only if there are secrets)
9. templates/ingress.yaml    (only if ingress_enabled is true)
10. templates/hpa.yaml       (only if hpa_enabled is true)

Rules:
- Use {{ "{{" }} and {{ "}}" }} for Helm template syntax properly
- Use .Values references everywhere (no hardcoded values in templates)
- Include proper labels: app.kubernetes.io/name, app.kubernetes.io/instance, etc.
- Chart.yaml must have apiVersion: v2
- values.yaml must have all configurable values
- _helpers.tpl must define {{ "{{" }}- define \"{meta['service_name']}.fullname\" -{{ "}}" }} and {{ "{{" }}- define \"{meta['service_name']}.labels\" -{{ "}}" }}
- deployment.yaml must have liveness and readiness probes using the health check path
- Include resource limits in deployment.yaml

Return JSON like:
{{
  "Chart.yaml": "...",
  "values.yaml": "...",
  ".helmignore": "...",
  "templates/_helpers.tpl": "...",
  "templates/deployment.yaml": "...",
  "templates/service.yaml": "...",
  "templates/configmap.yaml": "...",
  "templates/secret.yaml": "..."
}}"""

    try:
        response = llm.invoke([SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)])
        raw = response.content.strip()
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
        helm_files = json.loads(raw)
        logs.append(f"Generated {len(helm_files)} Helm chart files via LLM")
    except Exception as e:
        errors.append(f"Helm generation LLM call failed: {e}")
        helm_files = _fallback_helm_files(meta)
        logs.append("Used fallback Helm file generation")

    return {**state, "helm_files": helm_files, "logs": logs, "errors": errors, "status": "generation_done"}


def node_write_helm_files(state: HelmAgentState) -> HelmAgentState:
    logs = state.get("logs", [])
    errors = state.get("errors", [])

    service_name = state["service_metadata"].get("service_name", state["service_name"])
    base_path = Path(state["microservice_path"]) / "helm" / service_name
    written = []

    for rel_path, content in state["helm_files"].items():
        full_path = base_path / rel_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)
            written.append(str(full_path))
        except Exception as e:
            errors.append(f"Failed to write {rel_path}: {e}")

    logs.append(f"Written {len(written)} files to {base_path}")
    return {
        **state,
        "output_path": str(base_path),
        "logs": logs,
        "errors": errors,
        "status": "done",
    }


def _fallback_helm_files(meta: Dict[str, Any]) -> Dict[str, str]:
    name = meta.get("service_name", "myservice")
    repo = meta.get("image_repository", f"myorg/{name}")
    tag = meta.get("image_tag", "latest")
    port = meta.get("container_port", 8080)
    svc_port = meta.get("service_port", 80)
    replicas = meta.get("replicas", 1)
    cpu_req = meta.get("cpu_request", "100m")
    cpu_lim = meta.get("cpu_limit", "500m")
    mem_req = meta.get("memory_request", "128Mi")
    mem_lim = meta.get("memory_limit", "512Mi")
    health = meta.get("health_check_path", "/health")
    description = meta.get("description", "Auto-generated Helm chart")

    chart_yaml = f"""apiVersion: v2
name: {name}
description: {description}
type: application
version: 0.1.0
appVersion: "1.0.0"
"""

    values_yaml = f"""replicaCount: {replicas}

image:
  repository: {repo}
  tag: "{tag}"
  pullPolicy: IfNotPresent

service:
  type: {meta.get("service_type", "ClusterIP")}
  port: {svc_port}
  targetPort: {port}

resources:
  requests:
    cpu: {cpu_req}
    memory: {mem_req}
  limits:
    cpu: {cpu_lim}
    memory: {mem_lim}

livenessProbe:
  path: {health}
  initialDelaySeconds: 30
  periodSeconds: 10

readinessProbe:
  path: {health}
  initialDelaySeconds: 5
  periodSeconds: 5

ingress:
  enabled: {str(meta.get("ingress_enabled", False)).lower()}
  host: {meta.get("ingress_host", f"{name}.example.com")}

hpa:
  enabled: {str(meta.get("hpa_enabled", False)).lower()}
  minReplicas: {meta.get("min_replicas", 1)}
  maxReplicas: {meta.get("max_replicas", 3)}
  targetCPUUtilizationPercentage: 80

env: {{}}

serviceAccount:
  create: false
"""

    helpers_tpl = f"""{{{{- define "{name}.fullname" -}}}}
{{{{- if .Values.fullnameOverride }}}}
{{{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}}}
{{{{- else }}}}
{{{{- $name := default .Chart.Name .Values.nameOverride }}}}
{{{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}}}
{{{{- end }}}}
{{{{- end }}}}

{{{{- define "{name}.labels" -}}}}
helm.sh/chart: {{{{ include "{name}.chart" . }}}}
{{{{ include "{name}.selectorLabels" . }}}}
app.kubernetes.io/managed-by: {{{{ .Release.Service }}}}
{{{{- end }}}}

{{{{- define "{name}.selectorLabels" -}}}}
app.kubernetes.io/name: {{{{ include "{name}.fullname" . }}}}
app.kubernetes.io/instance: {{{{ .Release.Name }}}}
{{{{- end }}}}

{{{{- define "{name}.chart" -}}}}
{{{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}}}
{{{{- end }}}}
"""

    deployment_yaml = f"""apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{{{ include "{name}.fullname" . }}}}
  labels:
    {{{{- include "{name}.labels" . | nindent 4 }}}}
spec:
  replicas: {{{{ .Values.replicaCount }}}}
  selector:
    matchLabels:
      {{{{- include "{name}.selectorLabels" . | nindent 6 }}}}
  template:
    metadata:
      labels:
        {{{{- include "{name}.selectorLabels" . | nindent 8 }}}}
    spec:
      containers:
        - name: {{{{ .Chart.Name }}}}
          image: "{{{{ .Values.image.repository }}}}:{{{{ .Values.image.tag }}}}"
          imagePullPolicy: {{{{ .Values.image.pullPolicy }}}}
          ports:
            - name: http
              containerPort: {port}
              protocol: TCP
          livenessProbe:
            httpGet:
              path: {{{{ .Values.livenessProbe.path }}}}
              port: http
            initialDelaySeconds: {{{{ .Values.livenessProbe.initialDelaySeconds }}}}
            periodSeconds: {{{{ .Values.livenessProbe.periodSeconds }}}}
          readinessProbe:
            httpGet:
              path: {{{{ .Values.readinessProbe.path }}}}
              port: http
            initialDelaySeconds: {{{{ .Values.readinessProbe.initialDelaySeconds }}}}
            periodSeconds: {{{{ .Values.readinessProbe.periodSeconds }}}}
          resources:
            {{{{- toYaml .Values.resources | nindent 12 }}}}
          {{{{- if .Values.env }}}}
          env:
            {{{{- range $key, $val := .Values.env }}}}
            - name: {{{{ $key }}}}
              value: {{{{ $val | quote }}}}
            {{{{- end }}}}
          {{{{- end }}}}
"""

    service_yaml = f"""apiVersion: v1
kind: Service
metadata:
  name: {{{{ include "{name}.fullname" . }}}}
  labels:
    {{{{- include "{name}.labels" . | nindent 4 }}}}
spec:
  type: {{{{ .Values.service.type }}}}
  ports:
    - port: {{{{ .Values.service.port }}}}
      targetPort: {{{{ .Values.service.targetPort }}}}
      protocol: TCP
      name: http
  selector:
    {{{{- include "{name}.selectorLabels" . | nindent 4 }}}}
"""

    helmignore = """.DS_Store
.git/
.gitignore
.bzr/
.bzrignore
.hg/
.hgignore
.svn/
*.swp
*.bak
*.tmp
*.orig
*~
.project
.idea/
*.tmproj
.vscode/
"""

    return {
        "Chart.yaml": chart_yaml,
        "values.yaml": values_yaml,
        ".helmignore": helmignore,
        "templates/_helpers.tpl": helpers_tpl,
        "templates/deployment.yaml": deployment_yaml,
        "templates/service.yaml": service_yaml,
    }
