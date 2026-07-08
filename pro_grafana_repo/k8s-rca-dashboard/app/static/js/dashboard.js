const state = {
    summary: null,
    tree: null,
    alerts: [],
    timeline: [],
    rankings: {
        cpu: [],
        memory: [],
        network: [],
        disk: [],
    },
    nodes: [],
    selectedPodUid: null,
    podDetails: null,
};

const filters = {
    node: "all",
    namespace: "all",
    status: "all",
    search: "",
};

let filtersInitialized = false;
let nodeComparisonChart = null;
let resourceTrendsChart = null;

function setText(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
}

function buildSummaryCard(title, value, subtitle, badge) {
    return `
        <div class="summary-card">
            <h3>${title}</h3>
            <p>${value}</p>
            ${subtitle ? `<span>${subtitle}</span>` : ""}
        </div>
    `;
}

function renderSummary() {
    const container = document.getElementById("summary-grid");
    if (!container || !state.summary) return;

    container.innerHTML = [
        buildSummaryCard("Nodes", state.summary.total_nodes, `${state.summary.healthy_nodes} healthy`),
        buildSummaryCard("Pods", state.summary.total_pods, `${state.summary.running_pods} running`),
        buildSummaryCard("Alerts", state.summary.critical_alerts + state.summary.warning_alerts, `${state.summary.critical_alerts} critical, ${state.summary.warning_alerts} warning`),
        buildSummaryCard("Restarts (1h)", state.summary.recent_restarts_1h, state.summary.cluster_health.toUpperCase()),
    ].join("");
}

function filterTree(tree) {
    if (!tree || !tree.nodes) {
        return { nodes: [] };
    }

    const filteredNodes = tree.nodes
        .filter(node => filters.node === "all" || node.name === filters.node)
        .map(node => {
            const namespaces = node.namespaces
                .filter(ns => filters.namespace === "all" || ns.name === filters.namespace)
                .map(ns => {
                    const deployments = ns.deployments
                        .map(dep => {
                            const pods = dep.pods.filter(pod => {
                                const matchesStatus = filters.status === "all" || pod.phase === filters.status;
                                const query = filters.search.toLowerCase();
                                const matchesSearch = query === "" ||
                                    pod.name.toLowerCase().includes(query) ||
                                    pod.namespace.toLowerCase().includes(query) ||
                                    pod.node_name.toLowerCase().includes(query);
                                return matchesStatus && matchesSearch;
                            });
                            return { ...dep, pods };
                        })
                        .filter(dep => dep.pods.length > 0);
                    return { ...ns, deployments };
                })
                .filter(ns => ns.deployments.length > 0);
            return { ...node, namespaces };
        })
        .filter(node => node.namespaces.length > 0);

    return { nodes: filteredNodes };
}

function renderTree(tree) {
    const container = document.getElementById("cluster-tree");
    if (!container) return;

    const filtered = filterTree(tree);
    if (!filtered.nodes.length) {
        container.innerHTML = "<p>No cluster information found yet.</p>";
        return;
    }

    const html = filtered.nodes.map(node => {
        const namespaces = node.namespaces.map(ns => {
            const deployments = ns.deployments.map(dep => {
                const pods = dep.pods.map(pod => `
                    <div class="tree-node tree-pod-row${state.selectedPodUid === pod.uid ? " selected" : ""}" data-pod-uid="${pod.uid}">
                        <strong>${pod.name}</strong>
                        <span>${pod.namespace} · ${pod.phase} · ${pod.ready ? "Ready" : "NotReady"} · restarts ${pod.restart_count}</span>
                        <div class="tree-pod-meta">${pod.status_reason || "No status reason"}</div>
                    </div>
                `).join("");
                return `
                    <div class="tree-node tree-deployment-row">
                        <strong>${dep.kind}: ${dep.name}</strong>
                        <span>${dep.pod_count} pod(s)</span>
                        ${pods}
                    </div>
                `;
            }).join("");
            return `
                <div class="tree-node tree-namespace-row">
                    <strong>Namespace: ${ns.name}</strong>
                    ${deployments}
                </div>
            `;
        }).join("");
        return `
            <div class="tree-node tree-node-row">
                <strong>Node: ${node.name}</strong>
                <span>${node.status} · ${node.pod_count} pods</span>
                ${namespaces}
            </div>
        `;
    }).join("");

    container.innerHTML = html;
}

function filterAlerts(alerts) {
    const query = filters.search.toLowerCase();
    return alerts.filter(alert => {
        const matchesNode = filters.node === "all" || alert.node_name === filters.node;
        const matchesNamespace = filters.namespace === "all" || alert.namespace === filters.namespace;
        const matchesSearch = query === "" ||
            (alert.title && alert.title.toLowerCase().includes(query)) ||
            (alert.namespace && alert.namespace.toLowerCase().includes(query)) ||
            (alert.node_name && alert.node_name.toLowerCase().includes(query)) ||
            (alert.message && alert.message.toLowerCase().includes(query));
        return matchesNode && matchesNamespace && matchesSearch;
    });
}

function renderAlerts() {
    const container = document.getElementById("alerts-list");
    if (!container) return;

    const filtered = filterAlerts(state.alerts);
    if (!filtered.length) {
        container.innerHTML = "<p>No active alerts.</p>";
        return;
    }

    container.innerHTML = filtered.slice(0, 20).map(alert => `
        <div class="alert-row">
            <strong>${alert.title || alert.reason || alert.short_reason}</strong>
            <span>${alert.namespace} · ${alert.node_name} · ${new Date(alert.timestamp).toLocaleString()}</span>
            <p>${alert.message || alert.explanation || "No details available."}</p>
        </div>
    `).join("");
}

function filterTimeline(timeline) {
    const query = filters.search.toLowerCase();
    return timeline.filter(item => {
        const matchesNode = filters.node === "all" || item.node_name === filters.node;
        const matchesNamespace = filters.namespace === "all" || item.namespace === filters.namespace;
        const matchesStatus = filters.status === "all" || item.pod_phase === filters.status || item.phase === filters.status;
        const matchesSearch = query === "" ||
            (item.title && item.title.toLowerCase().includes(query)) ||
            (item.detail && item.detail.toLowerCase().includes(query)) ||
            (item.namespace && item.namespace.toLowerCase().includes(query)) ||
            (item.node_name && item.node_name.toLowerCase().includes(query));
        return matchesNode && matchesNamespace && matchesStatus && matchesSearch;
    });
}

function renderTimeline() {
    const container = document.getElementById("timeline-list");
    if (!container) return;

    const filtered = filterTimeline(state.timeline);
    if (!filtered.length) {
        container.innerHTML = "<p>No timeline events yet.</p>";
        return;
    }

    container.innerHTML = filtered.slice(0, 25).map(item => `
        <div class="timeline-item">
            <strong>[${item.kind}] ${item.title}</strong>
            <span>${item.namespace || item.node_name || ""} · ${new Date(item.timestamp).toLocaleString()}</span>
            <p>${item.detail || item.message || ""}</p>
        </div>
    `).join("");
}

function renderRankings() {
    [
        { id: "rankings-cpu", rows: state.rankings.cpu, formatter: r => `${r.name} — ${r.cpu_pct_of_limit ?? 0}%` },
        { id: "rankings-memory", rows: state.rankings.memory, formatter: r => `${r.name} — ${r.mem_pct_of_limit ?? 0}%` },
        { id: "rankings-network", rows: state.rankings.network, formatter: r => `${r.name} — ${r.net_rx_mbps ?? 0} / ${r.net_tx_mbps ?? 0} MB/s` },
        { id: "rankings-disk", rows: state.rankings.disk, formatter: r => `${r.name} — ${r.blk_total_mbps ?? 0} MB/s` },
    ].forEach(({ id, rows, formatter }) => {
        const el = document.getElementById(id);
        if (!el) return;
        if (!rows.length) {
            el.innerHTML = "<li>No data yet</li>";
            return;
        }
        el.innerHTML = rows.slice(0, 10).map(row => `<li class="ranking-item">${formatter(row)}</li>`).join("");
    });
}

function initCharts() {
    if (typeof echarts === "undefined") {
        console.warn("ECharts is not loaded; charts will be disabled.");
        return;
    }
    const nodeElement = document.getElementById("chart-node-comparison");
    const resourceElement = document.getElementById("chart-resource-trends");
    if (nodeElement) {
        nodeComparisonChart = echarts.init(nodeElement, null, { renderer: "canvas" });
    }
    if (resourceElement) {
        resourceTrendsChart = echarts.init(resourceElement, null, { renderer: "canvas" });
    }
}

function renderNodeComparisonChart() {
    if (!nodeComparisonChart || !state.nodes.length) return;
    const names = state.nodes.map(node => node.name);
    const cpu = state.nodes.map(node => node.metrics.cpu_pct ?? 0);
    const memory = state.nodes.map(node => node.metrics.mem_pct ?? 0);
    const disk = state.nodes.map(node => node.metrics.disk_pct ?? 0);
    const podCounts = state.nodes.map(node => node.pod_count_live ?? node.pod_count ?? 0);

    nodeComparisonChart.setOption({
        backgroundColor: "transparent",
        textStyle: { color: "#e2e8f0" },
        tooltip: { trigger: "axis" },
        legend: { data: ["CPU %", "Memory %", "Disk %", "Pods"], textStyle: { color: "#94a3b8" } },
        xAxis: {
            type: "category",
            data: names,
            axisLine: { lineStyle: { color: "rgba(148, 163, 184, 0.4)" } },
            axisLabel: { color: "#cbd5e1" },
        },
        yAxis: [
            { type: "value", name: "Percent", position: "left", axisLine: { lineStyle: { color: "rgba(148, 163, 184, 0.4)" } }, axisLabel: { color: "#cbd5e1" } },
            { type: "value", name: "Pods", position: "right", axisLine: { lineStyle: { color: "rgba(148, 163, 184, 0.4)" } }, axisLabel: { color: "#cbd5e1" } },
        ],
        series: [
            { name: "CPU %", type: "bar", data: cpu, itemStyle: { color: "#38bdf8" } },
            { name: "Memory %", type: "bar", data: memory, itemStyle: { color: "#facc15" } },
            { name: "Disk %", type: "bar", data: disk, itemStyle: { color: "#fb7185" } },
            { name: "Pods", type: "line", yAxisIndex: 1, data: podCounts, lineStyle: { color: "#34d399" }, symbol: "circle" },
        ],
    });
}

function renderResourceTrendsChart() {
    if (!resourceTrendsChart) return;
    const cpu = state.rankings.cpu.slice(0, 5);
    const memory = state.rankings.memory.slice(0, 5);
    const labels = cpu.map(item => item.name);
    resourceTrendsChart.setOption({
        backgroundColor: "transparent",
        textStyle: { color: "#e2e8f0" },
        tooltip: { trigger: "axis" },
        legend: { data: ["CPU %", "Memory %"], textStyle: { color: "#94a3b8" } },
        xAxis: {
            type: "category",
            data: labels,
            axisLine: { lineStyle: { color: "rgba(148, 163, 184, 0.4)" } },
            axisLabel: { color: "#cbd5e1" },
        },
        yAxis: {
            type: "value",
            axisLine: { lineStyle: { color: "rgba(148, 163, 184, 0.4)" } },
            axisLabel: { color: "#cbd5e1" },
        },
        series: [
            { name: "CPU %", type: "bar", data: cpu.map(item => item.cpu_pct_of_limit ?? 0), itemStyle: { color: "#38bdf8" } },
            { name: "Memory %", type: "bar", data: memory.map(item => item.mem_pct_of_limit ?? 0), itemStyle: { color: "#fb7185" } },
        ],
    });
}

function renderCharts() {
    renderNodeComparisonChart();
    renderResourceTrendsChart();
}

function setupFilters(tree) {
    state.tree = tree;
    const nodeSelect = document.getElementById("filter-node");
    const namespaceSelect = document.getElementById("filter-namespace");
    const statusSelect = document.getElementById("filter-status");
    const searchInput = document.getElementById("filter-search");
    const refreshButton = document.getElementById("refresh-button");

    if (!nodeSelect || !namespaceSelect || !statusSelect || !searchInput || !refreshButton) return;

    const nodes = (tree.nodes || []).map(node => node.name).filter(Boolean).sort();
    const namespaces = [...new Set((tree.nodes || []).flatMap(node => node.namespaces.map(ns => ns.name)))].filter(Boolean).sort();

    nodeSelect.innerHTML = `<option value="all">All nodes</option>${nodes.map(node => `<option value="${node}">${node}</option>`).join("")}`;
    namespaceSelect.innerHTML = `<option value="all">All namespaces</option>${namespaces.map(ns => `<option value="${ns}">${ns}</option>`).join("")}`;

    if (!filtersInitialized) {
        nodeSelect.addEventListener("change", event => {
            filters.node = event.target.value;
            renderTree(state.tree);
            renderAlerts();
            renderTimeline();
        });

        namespaceSelect.addEventListener("change", event => {
            filters.namespace = event.target.value;
            renderTree(state.tree);
            renderAlerts();
            renderTimeline();
        });

        statusSelect.addEventListener("change", event => {
            filters.status = event.target.value;
            renderTree(state.tree);
            renderAlerts();
            renderTimeline();
        });

        searchInput.addEventListener("input", event => {
            filters.search = event.target.value;
            renderTree(state.tree);
            renderAlerts();
            renderTimeline();
        });

        refreshButton.addEventListener("click", refreshData);
        filtersInitialized = true;
    }
}
function renderPodDetails() {
    const container = document.getElementById("pod-details");
    if (!container) return;

    if (!state.podDetails) {
        container.innerHTML = `<p>Select a pod from the cluster tree to see full details here.</p>`;
        return;
    }

    const pod = state.podDetails;
    const metrics = pod.cpu_metrics || {};
    const docker = pod.docker_metrics || {};
    const containers = pod.containers || [];
    const events = pod.recent_events || [];

    const hasPrometheusMetrics = metrics && Object.keys(metrics).length > 0;
    const hasDockerMetrics = docker && Object.keys(docker).length > 0;
    const cpuValue = hasPrometheusMetrics && metrics.cpu_pct_of_limit != null ? `${metrics.cpu_pct_of_limit.toFixed?.(2) ?? metrics.cpu_pct_of_limit}` : "-";
    const memValue = hasPrometheusMetrics && metrics.mem_pct_of_limit != null ? `${metrics.mem_pct_of_limit.toFixed?.(2) ?? metrics.mem_pct_of_limit}` : "-";
    const netRxValue = hasDockerMetrics && docker.net_rx_bytes_per_sec != null ? `${docker.net_rx_bytes_per_sec.toFixed(1)} B/s` : "-";
    const netTxValue = hasDockerMetrics && docker.net_tx_bytes_per_sec != null ? `${docker.net_tx_bytes_per_sec.toFixed(1)} B/s` : "-";

    container.innerHTML = `
        <div class="detail-panel">
            <h3>${pod.name}</h3>
            <p>${pod.namespace} / ${pod.node_name || "unscheduled"}</p>
            <div class="detail-grid">
                <div>
                    <dt>Phase</dt>
                    <dd>${pod.phase}</dd>
                </div>
                <div>
                    <dt>Status</dt>
                    <dd>${pod.status_reason || "N/A"}</dd>
                </div>
                <div>
                    <dt>Ready</dt>
                    <dd>${pod.ready ? "Yes" : "No"}</dd>
                </div>
                <div>
                    <dt>Restarts</dt>
                    <dd>${pod.restart_count}</dd>
                </div>
                <div>
                    <dt>Owner</dt>
                    <dd>${pod.owner_kind || "Pod"} / ${pod.owner_name || pod.name}</dd>
                </div>
                <div>
                    <dt>Pod IP</dt>
                    <dd>${pod.pod_ip || "-"}</dd>
                </div>
                <div>
                    <dt>Created</dt>
                    <dd>${pod.created_at || "-"}</dd>
                </div>
                <div>
                    <dt>Last updated</dt>
                    <dd>${pod.last_updated || "-"}</dd>
                </div>
            </div>
            <div class="detail-grid">
                <div>
                    <dt>CPU % of limit</dt>
                    <dd>${cpuValue}</dd>
                </div>
                <div>
                    <dt>Memory % of limit</dt>
                    <dd>${memValue}</dd>
                </div>
                <div>
                    <dt>Net RX</dt>
                    <dd>${netRxValue}</dd>
                </div>
                <div>
                    <dt>Net TX</dt>
                    <dd>${netTxValue}</dd>
                </div>
            </div>
            ${(!hasPrometheusMetrics || !hasDockerMetrics) ? `
                <div class="metric-note">
                    ${!hasPrometheusMetrics ? "Prometheus metrics unavailable for this pod. Check PROMETHEUS_URL and kube-state-metrics/cAdvisor access." : ""}
                    ${!hasDockerMetrics ? " Docker network metrics unavailable for this pod. Check DOCKER_HOSTS and Docker collector status." : ""}
                </div>
            ` : ""}
            <div class="detail-grid">
                <div>
                    <dt>Containers</dt>
                    <dd>${containers.length}</dd>
                </div>
                <div>
                    <dt>Recent events</dt>
                    <dd>${events.length}</dd>
                </div>
                <div>
                    <dt>PVCs</dt>
                    <dd>${pod.pvc_names && pod.pvc_names.length ? pod.pvc_names.join(", ") : "-"}</dd>
                </div>
                <div>
                    <dt>Labels</dt>
                    <dd>${pod.labels && Object.keys(pod.labels).length ? JSON.stringify(pod.labels) : "-"}</dd>
                </div>
            </div>
            <div>
                <h4>Container details</h4>
                <ul class="detail-list">
                    ${containers.length
            ? containers.map(c => `<li><strong>${c.container_name}</strong>: ${c.state || "unknown"} ${c.state_reason ? `(${c.state_reason})` : ""}, restarts ${c.restart_count}, ready ${c.ready ? "yes" : "no"}</li>`).join("")
            : `<li>No container details available.</li>`}
                </ul>
            </div>
            <div>
                <h4>Recent events</h4>
                <ul class="detail-list">
                    ${events.length
            ? events.slice(0, 5).map(ev => `<li><strong>${ev.reason || ev.title || "Event"}</strong>: ${ev.message || ev.detail || "no details"} <span>${ev.last_seen || ev.timestamp || ""}</span></li>`).join("")
            : `<li>No recent events available.</li>`}
                </ul>
            </div>
        </div>
    `;
}

async function loadPodDetails(uid) {
    state.selectedPodUid = uid;
    renderTree(state.tree);
    renderPodDetails();
    const url = `/api/pods/${encodeURIComponent(uid)}`;
    const details = await fetchJson(url);
    if (!details) return;
    state.podDetails = details;
    renderPodDetails();
}

function setupTreeSelection() {
    const container = document.getElementById("cluster-tree");
    if (!container) return;
    container.addEventListener("click", event => {
        const podRow = event.target.closest(".tree-pod-row");
        if (!podRow) return;
        const uid = podRow.dataset.podUid;
        if (!uid) return;
        if (state.selectedPodUid === uid) return;
        loadPodDetails(uid);
    });
}

async function fetchJson(path) {
    const resp = await fetch(path, { cache: "no-store" });
    if (!resp.ok) {
        console.error(`Failed to fetch ${path}:`, resp.statusText);
        return null;
    }
    return resp.json();
}

async function refreshData() {
    const results = await Promise.allSettled([
        fetchJson("/api/cluster/summary"),
        fetchJson("/api/cluster/tree"),
        fetchJson("/api/alerts?limit=20"),
        fetchJson("/api/events/timeline?minutes=120"),
        fetchJson("/api/rankings/all"),
        fetchJson("/api/nodes/compare"),
    ]);

    const [summaryResult, treeResult, alertsResult, timelineResult, rankingsResult, nodeCompareResult] = results;

    if (summaryResult.status === "fulfilled" && summaryResult.value) {
        state.summary = summaryResult.value;
        renderSummary();
    } else if (summaryResult.status === "rejected") {
        console.error("Failed to load cluster summary", summaryResult.reason);
    }

    if (treeResult.status === "fulfilled" && treeResult.value) {
        setupFilters(treeResult.value);
        renderTree(treeResult.value);
    } else if (treeResult.status === "rejected") {
        console.error("Failed to load cluster tree", treeResult.reason);
    }

    if (alertsResult.status === "fulfilled" && alertsResult.value) {
        state.alerts = alertsResult.value;
        renderAlerts();
    } else if (alertsResult.status === "rejected") {
        console.error("Failed to load alerts", alertsResult.reason);
    }

    if (timelineResult.status === "fulfilled" && timelineResult.value) {
        state.timeline = timelineResult.value;
        renderTimeline();
    } else if (timelineResult.status === "rejected") {
        console.error("Failed to load timeline", timelineResult.reason);
    }

    if (rankingsResult.status === "fulfilled" && rankingsResult.value) {
        state.rankings = rankingsResult.value;
        renderRankings();
    } else if (rankingsResult.status === "rejected") {
        console.error("Failed to load rankings", rankingsResult.reason);
    }

    if (nodeCompareResult.status === "fulfilled" && nodeCompareResult.value) {
        state.nodes = nodeCompareResult.value;
        renderCharts();
    } else if (nodeCompareResult.status === "rejected") {
        console.error("Failed to load node comparisons", nodeCompareResult.reason);
    }

    setText("last-updated", `updated: ${new Date().toLocaleTimeString()}`);
}

function setWsStatus(connected) {
    const el = document.getElementById("ws-status");
    if (!el) return;
    el.textContent = connected ? "WS: connected" : "WS: disconnected";
    el.className = connected ? "badge badge-healthy" : "badge badge-secondary";
}

function setupWebSocket() {
    const scheme = window.location.protocol === "https:" ? "wss" : "ws";
    const url = `${scheme}://${window.location.host}/ws`;
    const ws = new WebSocket(url);

    ws.addEventListener("open", () => {
        setWsStatus(true);
        console.info("WebSocket connected");
    });

    ws.addEventListener("message", event => {
        try {
            const payload = JSON.parse(event.data);
            if (payload.type === "alert") {
                state.alerts.unshift(payload.alert);
                state.timeline.unshift({
                    ...payload.alert,
                    kind: "rca",
                    title: payload.alert.title || payload.alert.short_reason || "New alert",
                    detail: payload.alert.message || payload.alert.explanation || "",
                });
                renderAlerts();
                renderTimeline();
            }
        } catch (err) {
            console.warn("Failed to parse WS message", err);
        }
    });

    ws.addEventListener("close", () => {
        setWsStatus(false);
        console.warn("WebSocket disconnected, retrying in 5s");
        setTimeout(setupWebSocket, 5000);
    });

    ws.addEventListener("error", () => {
        setWsStatus(false);
    });
}

window.addEventListener("load", async () => {
    initCharts();
    setupTreeSelection();
    await refreshData();
    renderPodDetails();
    setupWebSocket();
    setInterval(refreshData, 30_000);
});
