# Kubernetes RCA Dashboard: Deployment & Bug Fix Guide

This guide documents all the fixes, configurations, and commands executed to resolve the database locks, network routing issues, and empty pod metrics. You can use these steps to replicate the successful setup when deploying this application to another server.

---

## 1. Codebase Bug Fixes

### A. SQLite Concurrency Database Locks
* **Problem**: In concurrent scenarios, multiple background threads (collectors and evaluation engine) attempted to write to the SQLite database. Since SQLAlchemy by default starts SQLite transactions with `BEGIN DEFERRED`, connections upgraded their locks mid-transaction, causing frequent `OperationalError: (sqlite3.OperationalError) database is locked` crashes.
* **Fixes**:
  1. **Split Read/Write Engines**: Divided SQLite connections inside [app/core/database.py](file:///c:/rishabh/my_project/project15l/devops/pro_grafana_repo/k8s-rca-dashboard-updated2/k8s-rca-dashboard/app/core/database.py) into two separate engines:
     * `engine` / `SessionLocal`: Used by FastAPI GET requests for concurrent, non-blocking read-only queries (using standard `DEFERRED` transactions).
     * `write_engine` / `SessionLocalWrite`: Used by collectors and the background RCA engine to write to the database (configured with `isolation_level = None` and `@event.listens_for("begin")` emitting `BEGIN IMMEDIATE`).
  2. **Transaction-Level Retries**: Wrapped the RCA engine evaluation cycle in [app/rca/engine.py](file:///c:/rishabh/my_project/project15l/devops/pro_grafana_repo/k8s-rca-dashboard-updated2/k8s-rca-dashboard/app/rca/engine.py) in a transaction retry loop that rolls back, releases the session, and sleeps before retrying when a lock is encountered.
  3. **Isolation of Network Calls from Transactions**: Refactored [app/collectors/k8s_collector.py](file:///c:/rishabh/my_project/project15l/devops/pro_grafana_repo/k8s-rca-dashboard-updated2/k8s-rca-dashboard/app/collectors/k8s_collector.py) to execute all blocking Kubernetes API network calls (listing nodes, pods, events, and all ReplicaSets to map ownership cache) *outside* the database transaction, opening and committing the database session only at the very end for quick writes. This ensures database transactions are held open for milliseconds instead of seconds, completely avoiding locks during network latency.
  4. **Separation of Lookup Queries from Write Sessions**: Refactored `prometheus_collector.py`, `docker_collector.py`, and `longhorn_collector.py` to run all read-only lookup queries (such as querying the `Pod` list to resolve namespaces, names, and UIDs) inside a read-only `SessionLocal` connection pool instead of using the write session. Write sessions are now only used for quick mutation inserts/flushes, which releases SQLite locks instantly.

### B. SQLite `.one_or_none()` Multiple Results Found Crash
* **Problem**: In `k8s_collector.py`, querying pods mapping to events raised a `MultipleResultsFound` exception when multiple pod rows existed with the same name.
* **Fix**: Replaced `.one_or_none()` with `.order_by(Pod.last_updated.desc()).first()` to return the most recently updated pod row.

### C. Prometheus Exporter Label Fallback
* **Problem**: Metrics from Prometheus lacked a `"node"` label, resulting in empty metrics on the UI dashboard.
* **Fix**: Updated `prometheus_collector.py` to:
  - Check for `"node"` label; if missing, fall back to `"instance"` or `"kubernetes_node"`.
  - Automatically strip port suffixes (e.g. converting `"desktop-control-plane:9100"` to `"desktop-control-plane"`) so they match the node name in the database.

---

## 2. Cluster Network Routing Fixes

### A. Kube-Proxy Iptables-Restore WSL2 Compatibility
* **Problem**: On WSL2/Docker nodes, `kube-proxy` failed to synchronize iptables rules due to a missing kernel module (`xt_recent`), leaving the internal API service ClusterIP (`10.96.0.1:443`) unrouted and causing CoreDNS TLS handshakes to fail.
* **Solution**: Switch `kube-proxy` to use legacy iptables and high-performance `IPVS` load balancing.
* **Commands**:
  1. Set node container alternatives to legacy:
     ```bash
     docker exec desktop-control-plane update-alternatives --set iptables /usr/sbin/iptables-legacy
     docker exec desktop-control-plane update-alternatives --set ip6tables /usr/sbin/ip6tables-legacy
     ```
  2. Edit `kube-proxy` ConfigMap in `kube-system` namespace:
     ```bash
     kubectl edit configmap kube-proxy -n kube-system
     ```
     Change the `mode` parameter:
     ```yaml
     mode: ipvs
     ```
  3. Restart `kube-proxy` pods:
     ```bash
     kubectl delete pod -n kube-system -l k8s-app=kube-proxy
     ```

---

## 3. Metrics Exporter Configuration & Sidecar Bypass

By default, the exporters are bundled with `kube-rbac-proxy` sidecars that restrict connections to HTTPS/auth, causing plain HTTP Prometheus scrapes to fail with `400 Bad Request` or `Connection Refused`.

### A. Exposing Raw `node-exporter` Metrics
* **Fix**: Configure `node-exporter` to listen on all interfaces and bypass the secure proxy.
* **Patch Command / Config**:
  Modify the `node-exporter` DaemonSet to set container args and filter out the `kube-rbac-proxy` sidecar container:
  ```bash
  kubectl edit daemonset node-exporter -n monitoring
  ```
  - **Set Node-Exporter args**:
    ```yaml
    - --web.listen-address=0.0.0.0:9100
    ```
  - **Remove container**: Remove the `kube-rbac-proxy` block completely from the spec container list.

### B. Exposing Raw `kube-state-metrics` Metrics
* **Fix**: Configure the container to listen directly on `0.0.0.0:8080`.
* **Patch Command / Config**:
  ```bash
  kubectl edit deployment kube-state-metrics -n monitoring
  ```
  - **Set Container Args**:
    ```yaml
    - --host=0.0.0.0
    - --port=8080
    - --telemetry-host=0.0.0.0
    - --telemetry-port=8081
    ```
  - **Remove sidecars**: Remove `kube-rbac-proxy-main` and `kube-rbac-proxy-self` sidecars from the container list.

---

## 4. Pod CPU & Memory Scrapes (cAdvisor Integration)

* **Problem**: Pod dynamic usage stats (CPU millicores, Memory bytes) are collected by `cAdvisor` (built into the node Kubelet). The default simple Prometheus configuration was not scraping Kubelet/cAdvisor targets, resulting in `0%` usage values on the dashboard.
* **Fixes**:
  1. **Authorize Prometheus ServiceAccount**: Create a ClusterRoleBinding to grant the default service account in the `monitoring` namespace permissions to fetch the node proxy API:
     ```bash
     kubectl create clusterrolebinding prometheus-nodes-proxy --clusterrole=cluster-admin --serviceaccount=monitoring:default
     ```
  2. **Add cAdvisor Job to Prometheus Config**:
     Edit the `prometheus-config` ConfigMap:
     ```bash
     kubectl edit configmap prometheus-config -n monitoring
     ```
     Append the `kubernetes-cadvisor` job to `scrape_configs` under `prometheus.yml`:
     ```yaml
     - job_name: 'kubernetes-cadvisor'
       scheme: https
       tls_config:
         ca_file: /var/run/secrets/kubernetes.io/serviceaccount/ca.crt
         insecure_skip_verify: true
       bearer_token_file: /var/run/secrets/kubernetes.io/serviceaccount/token
       kubernetes_sd_configs:
         - role: node
       relabel_configs:
         - action: labelmap
           regex: __meta_kubernetes_node_label_(.+)
         - target_label: __address__
           replacement: kubernetes.default.svc:443
         - source_labels: [__meta_kubernetes_node_name]
           regex: (.+)
           target_label: __metrics_path__
           replacement: /api/v1/nodes/${1}/proxy/metrics/cadvisor
     ```
  3. **Apply and Restart**:
     ```bash
     kubectl delete pod -n monitoring -l app=simple-prometheus
     ```



# K3s Pod Failure & RCA Dashboard - Deployment Fixes & Enhancements

This document details all the changes, fixes, and architectural enhancements implemented in this codebase to resolve silent failures, add missing metrics, improve cluster tree grouping, and handle containerd/Docker configurations.

---

## 1. Backend Changes & Fixes

### A. ReplicaSet Grouping in Cluster Tree
* **File Modified:** `app/routers/cluster.py`
* **Fix/Feature:** Updated the cluster tree logic to group pods under their parent `Deployment`, `StatefulSet`, `DaemonSet`, or `ReplicaSet` if those properties are populated by the Kubernetes collector. This prevents replicas from being scattered or grouped under individual replica set hashes (e.g. `ReplicaSet: my-app-79cb8b9c44`), resulting in a cleaner and more aggregated tree layout.

### B. Prometheus Node IP-to-Name Resolution
* **Files Modified:** `app/core/config.py`, `app/collectors/k8s_collector.py`, `app/collectors/prometheus_collector.py`
* **Fix/Feature:** 
  - Defined a global thread-safe dict `NODE_IP_TO_NAME` inside configuration models.
  - The Kubernetes collector scans K8s node internal/external IP addresses and keeps `NODE_IP_TO_NAME` populated.
  - The Prometheus collector uses this dictionary to resolve target instances listed as raw IPs (like `192.168.42.213:9100`) back to actual K8s hostname node names (like `server-2`). This resolves missing CPU/Memory rankings and node comparison stats.
  - Resolved a silent `NameError` by importing the `Node` model inside `prometheus_collector.py`.

### C. cAdvisor Pod Network & Disk Throughput Scraper
* **File Modified:** `app/collectors/prometheus_collector.py`
* **Fix/Feature:** 
  - Added new PromQL queries executing a `5m` rate calculation for pod network RX/TX bytes and block IO device read/write throughput:
    - `sum by (namespace, pod) (rate(container_network_receive_bytes_total{pod!=""}[5m]))`
    - `sum by (namespace, pod) (rate(container_network_transmit_bytes_total{pod!=""}[5m]))`
    - `sum by (namespace, pod) (rate(container_blkio_device_usage_total{operation="Read", pod!=""}[5m]))`
    - `sum by (namespace, pod) (rate(container_blkio_device_usage_total{operation="Write", pod!=""}[5m]))`
  - Injected retrieved throughput figures into the `DockerMetric` DB table. This allows the dashboard to show network/disk IO metrics for pods without requiring a running Docker collector daemon (highly relevant for containerd-based systems like K3s/k3d).

### D. Auto-Detection of Local Docker Socket & Node Mapping
* **Files Modified:** `app/background.py`, `app/collectors/docker_collector.py`
* **Fix/Feature:**
  - If `DOCKER_HOSTS` configuration is empty, `background.py` attempts to connect to the local Docker socket (`unix://var/run/docker.sock`). If available, it automatically configures `DOCKER_HOSTS = {"localhost": "unix://var/run/docker.sock"}`.
  - In `docker_collector.py`, when collecting from a local docker socket, the node name would be written as `"localhost"`. To prevent node mismatch in DB joins, we pre-query the `Pod` table inside `_collect_node` and resolve the actual K8s node hostname (e.g. `server-2`) corresponding to the pod's UID before saving to database metrics.

### E. Robust Fallbacks for Longhorn Storage Collector
* **File Modified:** `app/collectors/longhorn_collector.py`
* **Fix/Feature:**
  - In many setups, standalone `/v1/replicas` or `/v1/engines` endpoints return empty arrays or fail. However, the volume object in `/v1/volumes` contains nested `"replicas"` and `"controllers"` (engines) arrays.
  - Updated `_merge` to use these nested objects as fallbacks.
  - In cases where the nested replica payload does not include a `currentState` label, the system deduces it from `"running": true/false` flag mapping to `"running"` / `"stopped"`.

---

## 2. Frontend Enhancements

### A. Responsive Time Window Selector
* **Files Modified:** `app/templates/index.html`, `app/static/css/dashboard.css`, `app/static/js/dashboard.js`
* **Fix/Feature:**
  - Added a **Time Window** selector dropdown to filter elements (options range from **Last 8m** to **Last 24h**).
  - Modified the filter layout CSS from a fixed 5-column grid to a responsive `repeat(auto-fit, minmax(150px, 1fr))` layout to accommodate the additional selector beautifully.
  - Attached events in `dashboard.js` to trigger a details redraw and timeline/refresh update using the selected time window.

### B. Pod Details Metrics Trend Line Chart
* **File Modified:** `app/static/js/dashboard.js`
* **Fix/Feature:**
  - Configured `loadPodDetails` to request metrics history and docker statistics history in parallel from the backend, constrained by the selected Time Window.
  - Initialized a dual-axis ECharts line chart inside the **Pod Details** section showing historical usage:
    - **Left Axis**: Network RX/TX, Disk Read/Write rates in MB/s.
    - **Right Axis**: CPU & Memory usage percentage.
  - Instantly disposes and redraws when selecting a new pod or altering the time filter.

### C. Persistent Toast Notifications
* **File Modified:** `app/static/js/dashboard.js`
* **Fix/Feature:** Increased the toast duration parameter inside `showToast()` to **30 seconds** (previously 12s) to prevent warning details from disappearing before they can be read.

---

## 3. Platform Compatibility (Windows/WSL2)

### A. Windows Named Pipes for Docker API
* **Problem**: Running `main.py` on a Windows host caused the auto-detect logic to fail with `AttributeError: module 'socket' has no attribute 'AF_UNIX'` when trying to open `unix://var/run/docker.sock`.
* **Fix**: Updated `background.py` to inspect `os.name`. If Windows (`nt`), it checks the local named pipe `npipe:////./pipe/docker_engine` instead. On Unix/Linux, it continues using the standard socket (`unix://var/run/docker.sock`), allowing seamless container metrics fetching regardless of the host OS.

---

## 4. Kubernetes Diagnostics & Volume Tests

### A. Longhorn Single-Node Setup Configuration
* **File Created:** `longhorn-sc.yaml`
* **Action:** Deployed a custom StorageClass named `longhorn-single` setting `numberOfReplicas: "1"`. This allows Longhorn volumes to schedule successfully on single-node WSL2/kind development environments (default SC requires 3 replicas on 3 separate nodes).

### B. iSCSI kernel module limitations in WSL2
* **Diagnosed issue:** WSL2 runs on a stripped-down kernel compilation that lacks iSCSI target/initiator support. The Longhorn engine failed to mount the volume, throwing iscsiadm errors (`can not connect to iSCSI daemon (111)`).
* **Fix/Recommendation:** Using Rancher **Local Path Provisioner** (`standard` / `hostpath`) for local WSL2 volume testing (which verified correctly and preserves logs on pod restarts) while leaving Longhorn ready to mount on staging/production environments that run full Linux kernels with `iscsi_tcp` loaded.

