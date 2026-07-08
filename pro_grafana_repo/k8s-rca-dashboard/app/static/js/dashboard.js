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
};

const filters = {
    node: "all",
    namespace: "all",
    status: "all",
    search: "",
};

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
                    <div class="tree-node">
                        <strong>${pod.name}</strong>
                        <span>${pod.namespace} · ${pod.phase} · restarts ${pod.restart_count}</span>
                    </div>
                `).join("");
                return `
                    <div class="tree-node">
                        <strong>${dep.kind}: ${dep.name}</strong>
                        <span>${dep.pod_count} pod(s)</span>
                        ${pods}
                    </div>
                `;
            }).join("");
            return `
                <div class="tree-node">
                    <strong>Namespace: ${ns.name}</strong>
                    ${deployments}
                </div>
            `;
        }).join("");
        return `
            <div class="tree-node">
                <strong>Node: ${node.name} (${node.status})</strong>
                <span>${node.pod_count} pods</span>
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
    const searchInput = document.getElementById("filter-search");
    const refreshButton = document.getElementById("refresh-button");

    if (!nodeSelect || !namespaceSelect || !searchInput || !refreshButton) return;

    const nodes = tree.nodes.map(node => node.name);
    const namespaces = [...new Set(tree.nodes.flatMap(node => node.namespaces.map(ns => ns.name)))].sort();

    nodeSelect.innerHTML = `<option value="all">All nodes</option>${nodes.map(node => `<option value="${node}">${node}</option>`).join("")}`;
    namespaceSelect.innerHTML = `<option value="all">All namespaces</option>${namespaces.map(ns => `<option value="${ns}">${ns}</option>`).join("")}`;

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

    searchInput.addEventListener("input", event => {
        filters.search = event.target.value;
        renderTree(state.tree);
        renderAlerts();
        renderTimeline();
    });

    refreshButton.addEventListener("click", refreshData);
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
    const [summary, tree, alerts, timeline, rankings, nodeCompare] = await Promise.all([
        fetchJson("/api/cluster/summary"),
        fetchJson("/api/cluster/tree"),
        fetchJson("/api/alerts?limit=20"),
        fetchJson("/api/events/timeline?minutes=120"),
        fetchJson("/api/rankings/all"),
        fetchJson("/api/nodes/compare"),
    ]);

    if (summary) {
        state.summary = summary;
        renderSummary();
    }
    if (tree) {
        setupFilters(tree);
        renderTree(tree);
    }
    if (alerts) {
        state.alerts = alerts;
        renderAlerts();
    }
    if (timeline) {
        state.timeline = timeline;
        renderTimeline();
    }
    if (rankings) {
        state.rankings = rankings;
        renderRankings();
    }
    if (nodeCompare) {
        state.nodes = nodeCompare;
        renderCharts();
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
    await refreshData();
    setupWebSocket();
    setInterval(refreshData, 30_000);
});
