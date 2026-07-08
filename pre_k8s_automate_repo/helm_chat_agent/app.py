import os
import sys
import time
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(Path(__file__).parent))

from agent.graph import build_helm_agent
from agent.state import HelmAgentState

st.set_page_config(
    page_title="Helm Chart Generator Agent",
    page_icon="⛵",
    layout="wide",
)

# ─── Styles ──────────────────────────────────────────────────────────────────
st.markdown("""
<style>
[data-testid="stAppViewContainer"] {
    background: linear-gradient(135deg, #0f0c29, #302b63, #24243e);
    color: #e0e0e0;
}
.main-card {
    background: rgba(255,255,255,0.05);
    border-radius: 16px;
    padding: 2rem;
    border: 1px solid rgba(255,255,255,0.1);
    margin-bottom: 1.5rem;
}
.step-box {
    background: rgba(0,200,150,0.08);
    border-left: 4px solid #00c896;
    border-radius: 8px;
    padding: 0.75rem 1rem;
    margin: 0.4rem 0;
    font-size: 0.9rem;
}
.error-box {
    background: rgba(255,80,80,0.1);
    border-left: 4px solid #ff5050;
    border-radius: 8px;
    padding: 0.75rem 1rem;
    margin: 0.4rem 0;
    font-size: 0.9rem;
}
.file-card {
    background: rgba(0,0,0,0.35);
    border-radius: 10px;
    padding: 1rem;
    border: 1px solid rgba(255,255,255,0.08);
    margin-bottom: 0.5rem;
}
.metric-chip {
    display: inline-block;
    background: rgba(0,200,150,0.15);
    border: 1px solid #00c896;
    border-radius: 20px;
    padding: 0.2rem 0.8rem;
    font-size: 0.78rem;
    margin: 0.2rem;
    color: #00c896;
}
h1, h2, h3 { color: #ffffff !important; }
label { color: #cccccc !important; }
</style>
""", unsafe_allow_html=True)

# ─── Header ──────────────────────────────────────────────────────────────────
if view_mode == "⚡ Generate Helm Chart":
    st.markdown("""
    <div style='text-align:center; padding: 1.5rem 0 0.5rem;'>
        <span style='font-size:3rem;'>⛵</span>
        <h1 style='margin:0; font-size:2.4rem; background: linear-gradient(90deg,#00c896,#7b61ff);
                   -webkit-background-clip:text; -webkit-text-fill-color:transparent;'>
            Helm Chart Generator Agent
        </h1>
        <p style='color:#aaa; margin-top:0.3rem;'>
            Point to any microservice → the AI agent auto-generates a production Helm chart
        </p>
    </div>
    """, unsafe_allow_html=True)
else:
    st.markdown("""
    <div style='text-align:center; padding: 1.5rem 0 0.5rem;'>
        <span style='font-size:3rem;'>📘</span>
        <h1 style='margin:0; font-size:2.4rem; background: linear-gradient(90deg,#00c896,#7b61ff);
                   -webkit-background-clip:text; -webkit-text-fill-color:transparent;'>
            Documentation
        </h1>
        <p style='color:#aaa; margin-top:0.3rem;'>
            Complete documentation for the Helm Chart Generator Agent
        </p>
    </div>
    """, unsafe_allow_html=True)

# ─── Sidebar: Config ─────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### ⚙️ Configuration")
    api_key_input = st.text_input(
        "Google Gemini API Key",
        value=os.getenv("GOOGLE_API_KEY", ""),
        type="password",
        help="Get your key at https://aistudio.google.com/",
    )
    if api_key_input:
        os.environ["GOOGLE_API_KEY"] = api_key_input

    st.markdown("---")
    view_mode = st.radio("View", ["⚡ Generate Helm Chart", "📘 Documentation"], label_visibility="collapsed")

    st.markdown("---")
    st.markdown("### 📋 What the Agent Does")
    steps = [
        "🔍 Scans microservice directory",
        "📄 Reads `.env`, `README.md`, `Dockerfile`",
        "🧠 Analyzes via Gemini LLM",
        "⛵ Generates full Helm chart",
        "💾 Writes files to `helm/<service>/`",
    ]
    for s in steps:
        st.markdown(f"<div class='step-box'>{s}</div>", unsafe_allow_html=True)

    st.markdown("---")
    st.markdown("### 📁 Generated Structure")
    st.code("""helm/<service>/
├── Chart.yaml
├── values.yaml
├── .helmignore
└── templates/
    ├── _helpers.tpl
    ├── deployment.yaml
    ├── service.yaml
    ├── configmap.yaml
    ├── secret.yaml
    ├── ingress.yaml
    └── hpa.yaml""", language="")

# ─── Documentation View ───────────────────────────────────────────────────────
if view_mode == "📘 Documentation":
    doc_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "DOCUMENTATION.md")
    if os.path.exists(doc_path):
        with open(doc_path, "r", encoding="utf-8") as f:
            doc_content = f.read()
        st.markdown(doc_content)
    else:
        st.error("DOCUMENTATION.md file not found.")
    st.stop()

# ─── Main Input ──────────────────────────────────────────────────────────────
st.markdown("<div class='main-card'>", unsafe_allow_html=True)
st.markdown("### 🎯 Microservice Path")
col1, col2, col3 = st.columns([3, 1, 0.5])
with col1:
    microservice_path = st.text_input(
        "Enter the absolute path to your microservice folder",
        placeholder=r"C:\projects\my-api  or  /home/user/services/auth-service",
        label_visibility="collapsed",
    )
with col2:
    generate_btn = st.button("⚡ Generate Helm Chart", use_container_width=True, type="primary")
with col3:
    if st.button("ℹ️", help="What is this?"):
        st.session_state.show_helm_info = not st.session_state.get("show_helm_info", False)

if st.session_state.get("show_helm_info", False):
    with st.expander("ℹ️ What, Why, How: Helm Chart Generation", expanded=True):
        st.markdown("""
**What:** Automatically generate production-ready Kubernetes Helm charts for any microservice.

**Why:** Helm charts are essential for:
- Consistent deployments across environments (dev, staging, prod)
- Version-controlled infrastructure as code
- Easy rollback and upgrade management
- Standardized configuration management

**How:** This agent workflow:
- **Read**: Scans `.env`, `README.md`, `Dockerfile`, `requirements.txt`/`package.json`
- **Analyze**: Uses Gemini LLM to extract service metadata (ports, image, env vars)
- **Generate**: Creates all Helm chart files (Chart.yaml, values.yaml, templates)
- **Write**: Writes `helm/<service>/` folder to your microservice directory

The generated chart includes deployment, service, configmap, secret, ingress, and HPA manifests.
        """)

if microservice_path and os.path.isdir(microservice_path):
    st.success(f"✅ Directory found: `{microservice_path}`")
elif microservice_path:
    st.error("❌ Directory not found. Please enter a valid path.")
st.markdown("</div>", unsafe_allow_html=True)

# ─── Run Agent ───────────────────────────────────────────────────────────────
if generate_btn:
    if not microservice_path:
        st.error("Please enter a microservice path.")
        st.stop()
    if not os.path.isdir(microservice_path):
        st.error("Directory does not exist.")
        st.stop()
    if not os.getenv("GOOGLE_API_KEY"):
        st.error("Please set your Google Gemini API Key in the sidebar.")
        st.stop()

    st.markdown("---")
    st.markdown("## 🚀 Agent Running")

    progress = st.progress(0, text="Initializing agent…")
    status_container = st.empty()
    log_container = st.container()

    stage_labels = {
        "read": ("🔍 Reading microservice files…", 20),
        "analyze": ("🧠 Analyzing with Gemini LLM…", 50),
        "generate": ("⚙️ Generating Helm chart files…", 80),
        "write": ("💾 Writing files to disk…", 95),
        "done": ("✅ Complete!", 100),
    }

    with st.spinner("Agent is working…"):
        try:
            agent = build_helm_agent()
            initial_state: HelmAgentState = {
                "microservice_path": microservice_path,
                "service_name": "",
                "env_content": "",
                "readme_content": "",
                "dockerfile_content": "",
                "dependency_file_content": "",
                "directory_tree": "",
                "service_metadata": {},
                "helm_files": {},
                "output_path": "",
                "errors": [],
                "logs": [],
                "status": "init",
            }

            final_state = None
            for step_output in agent.stream(initial_state):
                node_name = list(step_output.keys())[0]
                node_state = step_output[node_name]

                label, pct = stage_labels.get(node_name, ("Processing…", 60))
                progress.progress(pct / 100, text=label)
                status_container.info(f"**Current step:** {label}")

                with log_container:
                    for log in node_state.get("logs", [])[-3:]:
                        st.markdown(f"<div class='step-box'>📌 {log}</div>", unsafe_allow_html=True)
                    for err in node_state.get("errors", []):
                        st.markdown(f"<div class='error-box'>⚠️ {err}</div>", unsafe_allow_html=True)

                final_state = node_state

            progress.progress(1.0, text="✅ Done!")
            status_container.empty()

        except Exception as e:
            st.error(f"Agent error: {e}")
            st.stop()

    # ─── Results ─────────────────────────────────────────────────────────────
    if final_state and final_state.get("status") == "done":
        st.success(f"🎉 Helm chart generated at: `{final_state['output_path']}`")

        meta = final_state.get("service_metadata", {})
        helm_files = final_state.get("helm_files", {})

        st.markdown("## 📊 Service Metadata")
        chips_html = ""
        for k, v in [
            ("Service", meta.get("service_name", "—")),
            ("Image", f"{meta.get('image_repository','—')}:{meta.get('image_tag','—')}"),
            ("Port", meta.get("container_port", "—")),
            ("Replicas", meta.get("replicas", "—")),
            ("Language", meta.get("language", "—")),
            ("CPU", f"{meta.get('cpu_request','—')} / {meta.get('cpu_limit','—')}"),
            ("Memory", f"{meta.get('memory_request','—')} / {meta.get('memory_limit','—')}"),
            ("Health", meta.get("health_check_path", "—")),
        ]:
            chips_html += f"<span class='metric-chip'><b>{k}:</b> {v}</span>"
        st.markdown(chips_html, unsafe_allow_html=True)

        if meta.get("description"):
            st.markdown(f"> 📝 {meta['description']}")

        st.markdown("---")
        st.markdown(f"## 📁 Generated Files ({len(helm_files)} files)")

        for rel_path, content in sorted(helm_files.items()):
            with st.expander(f"📄 `{rel_path}`", expanded=False):
                ext = Path(rel_path).suffix
                lang_map = {".yaml": "yaml", ".yml": "yaml", ".tpl": "yaml", ".json": "json"}
                lang = lang_map.get(ext, "text")
                st.code(content, language=lang)

        st.markdown("---")
        st.markdown("## ⚡ Quick Commands")
        svc_name = meta.get("service_name", "myservice")
        out_path = final_state["output_path"]
        st.code(f"""# Lint the chart
helm lint {out_path}

# Dry-run install
helm install {svc_name} {out_path} --dry-run --debug

# Install to cluster
helm install {svc_name} {out_path} -n default

# Upgrade
helm upgrade {svc_name} {out_path} -n default

# Uninstall
helm uninstall {svc_name} -n default""", language="bash")

        if final_state.get("errors"):
            st.warning("⚠️ Some non-fatal issues occurred:")
            for e in final_state["errors"]:
                st.markdown(f"<div class='error-box'>{e}</div>", unsafe_allow_html=True)

    elif final_state and final_state.get("status") == "error":
        st.error("Agent encountered errors:")
        for e in final_state.get("errors", []):
            st.error(e)
    else:
        st.warning("Agent completed but status is unknown.")

# ─── Footer ──────────────────────────────────────────────────────────────────
st.markdown("---")
st.markdown("""
<div style='text-align:center; color:#666; font-size:0.8rem; padding:1rem;'>
    Helm Chart Generator Agent • Powered by Gemini + LangGraph + LangChain
</div>
""", unsafe_allow_html=True)
