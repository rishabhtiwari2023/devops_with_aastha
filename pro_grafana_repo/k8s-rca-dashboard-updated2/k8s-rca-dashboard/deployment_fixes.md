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
