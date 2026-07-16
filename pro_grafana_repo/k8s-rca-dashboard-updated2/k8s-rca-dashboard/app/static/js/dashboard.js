const state = {
    summary: null,
    tree: null,
    alerts: [],
    alertsPage: 1,
    alertsPages: 1,
    timeline: [],
    timelinePage: 1,
    timelinePages: 1,
    rankings: {
        cpu: [],
        memory: [],
        network: [],
        disk: [],
    },
    nodes: [],
    failures: [],
    clusterResources: null,
    collectorHealth: null,
    selectedPodUid: null,
    podDetails: null,
    expandedNodes: new Set(),
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

function formatDate(ts) {
    if (!ts) return "";
    const d = new Date(ts);
    if (isNaN(d.getTime())) return String(ts);
    return d.toLocaleString();
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

// --- NEW: collector / data-source health strip -----------------------
function renderCollectorHealth() {
    const container = document.getElementById("collector-health-badges");
    if (!container || !state.collectorHealth) return;

    const badges = state.collectorHealth.collectors.map(c => {
        const label = c.status === "ok" ? "OK"
            : c.status === "disabled" ? "disabled"
            : c.status === "no_data" ? "no data"
            : c.status === "stale" ? "stale"
            : c.status;
        return `<span class="collector-badge status-${c.status}" title="${escapeAttr(c.reason)}">${c.name}: ${label}</span>`;
    }).join("");

    container.innerHTML = badges;

    if (!state.collectorHealth.cpu_ram_disk_metrics_available) {
        const promCollector = state.collectorHealth.collectors.find(c => c.name === "prometheus");
        if (promCollector) {
            container.innerHTML += `<span class="collector-badge status-disabled" title="${escapeAttr(promCollector.reason)}">ℹ CPU/RAM/Disk metrics need Prometheus — hover collectors above for details</span>`;
        }
    }
}

function escapeAttr(str) {
    return (str || "").replace(/"/g, "&quot;");
}

// --- NEW: cluster-wide CPU/RAM/Disk resource cards --------------------
function levelClass(pct) {
    if (pct >= 90) return "level-critical";
    if (pct >= 75) return "level-warning";
    return "";
}

function renderClusterResources() {
    const r = state.clusterResources;
    if (!r) return;

    const setBar = (fillId, textId, pct, extraText) => {
        const fill = document.getElementById(fillId);
        const text = document.getElementById(textId);
        if (fill) {
            fill.style.width = `${Math.min(100, pct).toFixed(1)}%`;
            fill.className = `resource-bar-fill ${levelClass(pct)}`;
        }
        if (text) text.textContent = extraText;
    };

    if (!r.metrics_available) {
        setBar("resource-fill-cpu", "resource-text-cpu", 0, "No data — Prometheus not reporting (see Data sources above)");
        setBar("resource-fill-mem", "resource-text-mem", 0, "No data — Prometheus not reporting");
        setBar("resource-fill-disk", "resource-text-disk", 0, "No data — Prometheus not reporting");
        return;
    }

    setBar("resource-fill-cpu", "resource-text-cpu", r.cluster_avg_cpu_pct, `${r.cluster_avg_cpu_pct.toFixed(1)}% across ${r.total_nodes} node(s)`);
    setBar("resource-fill-mem", "resource-text-mem", r.cluster_avg_mem_pct, `${r.cluster_avg_mem_pct.toFixed(1)}% (${formatBytes(r.total_mem_used_bytes)} / ${formatBytes(r.total_mem_total_bytes)})`);
    setBar("resource-fill-disk", "resource-text-disk", r.cluster_avg_disk_pct, `${r.cluster_avg_disk_pct.toFixed(1)}% (${formatBytes(r.total_disk_used_bytes)} / ${formatBytes(r.total_disk_total_bytes)})`);
}

function formatBytes(n) {
    n = Number(n) || 0;
    const units = ["B", "KB", "MB", "GB", "TB"];
    let i = 0;
    while (n >= 1024 && i < units.length - 1) {
        n /= 1024;
        i += 1;
    }
    return `${n.toFixed(1)}${units[i]}`;
}

// --- NEW: node table (CPU/RAM/Disk/pods/conditions per node) ----------
function renderNodeTable() {
    const container = document.getElementById("node-table");
    if (!container) return;
    if (!state.nodes.length) {
        container.innerHTML = "<p>No node data yet.</p>";
        return;
    }

    const rows = state.nodes.map(node => {
        const m = node.metrics || {};
        const hasMetrics = Object.keys(m).length > 0;
        const statusClass = node.status === "Ready" ? "ready" : "notready";
        const cpu = hasMetrics && m.cpu_pct != null ? `${m.cpu_pct.toFixed(1)}%` : "-";
        const mem = hasMetrics && m.mem_pct != null ? `${m.mem_pct.toFixed(1)}%` : "-";
        const disk = hasMetrics && m.disk_pct != null ? `${m.disk_pct.toFixed(1)}%` : "-";
        const pressure = hasMetrics
            ? [m.memory_pressure ? "Mem" : "", m.disk_pressure ? "Disk" : "", m.pid_pressure ? "PID" : ""].filter(Boolean).join(", ") || "none"
            : "-";
        return `
            <tr>
                <td>${node.name}</td>
                <td><span class="node-status-pill ${statusClass}">${node.status}</span></td>
                <td>${node.roles || "-"}</td>
                <td>${node.pod_count_live ?? node.pod_count ?? 0}</td>
                <td>${cpu}</td>
                <td>${mem}</td>
                <td>${disk}</td>
                <td>${pressure}</td>
            </tr>
        `;
    }).join("");

    container.innerHTML = `
        <table>
            <thead>
                <tr>
                    <th>Node</th><th>Status</th><th>Roles</th><th>Pods</th>
                    <th>CPU</th><th>Memory</th><th>Disk</th><th>Pressure</th>
                </tr>
            </thead>
            <tbody>${rows}</tbody>
        </table>
        ${!state.nodes.some(n => n.metrics && Object.keys(n.metrics).length) ? '<p class="metric-note">CPU/Memory/Disk columns are empty because Prometheus metrics aren\'t available yet — see the "Data sources" strip above for why.</p>' : ""}
    `;
}

// --- NEW: pod failures & root-cause reasons feed -----------------------
function renderFailures() {
    const container = document.getElementById("failures-list");
    if (!container) return;

    if (!state.failures.length) {
        container.innerHTML = "<p>No failing or crash-looping pods right now. 🎉</p>";
        return;
    }

    container.innerHTML = state.failures.slice(0, 20).map(f => {
        const rc = f.root_cause;
        const severity = rc ? rc.severity : "info";
        const reasonHtml = rc
            ? `<p class="failure-reason"><span class="severity-pill ${severity}">${severity}</span>${rc.short_reason}</p>`
            : `<p class="failure-reason"><span class="severity-pill info">pending</span>No root-cause verdict yet.</p>`;
        const explanation = rc ? rc.explanation : "The RCA engine evaluates every few seconds.";
        return `
            <details class="failure-row" data-pod-uid="${f.pod_uid || f.uid}">
                <summary class="failure-summary-header">
                    <strong>${f.pod_name || f.name}</strong>
                    <span class="failure-meta">${f.namespace} · ${f.node_name || "unscheduled"} · ${f.phase}${f.restart_count != null ? ` · restarts ${f.restart_count}` : ""}</span>
                    ${reasonHtml}
                </summary>
                <div class="failure-details-content">
                    <p>${explanation}</p>
                </div>
            </details>
        `;
    }).join("");
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
        const nodeKey = `node:${node.name}`;
        // Nodes default to open; if key is in set, it means it was collapsed by the user
        const isNodeCollapsed = state.expandedNodes.has(nodeKey);
        const nodeCollapsedClass = isNodeCollapsed ? " collapsed" : "";

        const namespaces = node.namespaces.map(ns => {
            const nsKey = `namespace:${ns.name}`;
            // Namespaces default to closed; if key is in set, it means it is expanded
            const isNsExpanded = state.expandedNodes.has(nsKey);
            const nsCollapsedClass = isNsExpanded ? "" : " collapsed";

            const deployments = ns.deployments.map(dep => {
                const depKey = `deployment:${ns.name}:${dep.name}`;
                // Deployments default to closed; if key is in set, it means it is expanded
                const isDepExpanded = state.expandedNodes.has(depKey);
                const depCollapsedClass = isDepExpanded ? "" : " collapsed";

                const pods = dep.pods.map(pod => `
                    <div class="tree-node tree-pod-row${state.selectedPodUid === pod.uid ? " selected" : ""}" data-pod-uid="${pod.uid}">
                        <strong>${pod.name}</strong>
                        <span>${pod.namespace} · ${pod.phase} · ${pod.ready ? "Ready" : "NotReady"} · restarts ${pod.restart_count}</span>
                        <div class="tree-pod-meta">${pod.status_reason || "No status reason"}</div>
                    </div>
                `).join("");
                return `
                    <div class="tree-node tree-deployment-row${depCollapsedClass}" data-tree-key="${depKey}">
                        <strong>${dep.kind}: ${dep.name}</strong>
                        <span>${dep.pod_count} pod(s)</span>
                        ${pods}
                    </div>
                `;
            }).join("");
            return `
                <div class="tree-node tree-namespace-row${nsCollapsedClass}" data-tree-key="${nsKey}">
                    <strong>Namespace: ${ns.name}</strong>
                    ${deployments}
                </div>
            `;
        }).join("");
        return `
            <div class="tree-node tree-node-row${nodeCollapsedClass}" data-tree-key="${nodeKey}">
                <strong>Node: ${node.name}</strong>
                <span>${node.status} · ${node.pod_count} pods</span>
                ${namespaces}
            </div>
        `;
    }).join("");

    container.innerHTML = html;
}

function renderAlerts() {
    const container = document.getElementById("alerts-list");
    if (!container) return;

    if (!state.alerts.length) {
        container.innerHTML = "<p>No active alerts.</p>";
        return;
    }

    container.innerHTML = state.alerts.map(alert => `
        <details class="alert-row">
            <summary class="alert-summary-header">
                <strong>${alert.title || alert.reason || alert.short_reason}</strong>
                <span>${alert.namespace} · ${alert.node_name} · ${formatDate(alert.timestamp)}</span>
            </summary>
            <div class="alert-details-content">
                <p>${alert.message || alert.explanation || "No details available."}</p>
            </div>
        </details>
    `).join("");
}

function renderTimeline() {
    const container = document.getElementById("timeline-list");
    if (!container) return;

    if (!state.timeline.length) {
        container.innerHTML = "<p>No timeline events yet.</p>";
        return;
    }

    container.innerHTML = state.timeline.map(item => `
        <details class="timeline-item">
            <summary class="timeline-summary-header">
                <strong>[${item.kind}] ${item.title}</strong>
                <span>${item.namespace || item.node_name || ""} · ${formatDate(item.timestamp)}</span>
            </summary>
            <div class="timeline-details-content">
                <p>${item.detail || item.message || "No details available."}</p>
            </div>
        </details>
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
        el.innerHTML = rows.map(row => `<li class="ranking-item">${formatter(row)}</li>`).join("");
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
        });

        namespaceSelect.addEventListener("change", event => {
            filters.namespace = event.target.value;
            renderTree(state.tree);
        });

        statusSelect.addEventListener("change", event => {
            filters.status = event.target.value;
            renderTree(state.tree);
        });

        searchInput.addEventListener("input", event => {
            filters.search = event.target.value;
            renderTree(state.tree);
        });

        const timeSelect = document.getElementById("filter-time");
        if (timeSelect) {
            timeSelect.addEventListener("change", () => {
                if (state.selectedPodUid) {
                    loadPodDetails(state.selectedPodUid);
                }
                refreshData();
            });
        }

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
    const rootCause = pod.root_cause || null;

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
            ${rootCause ? `
                <div class="root-cause-card severity-${rootCause.severity}">
                    <h4><span class="severity-pill ${rootCause.severity}">${rootCause.severity}</span>${rootCause.short_reason}</h4>
                    <p>${rootCause.explanation}</p>
                    ${rootCause.evidence ? `
                        <div class="evidence-block" style="margin-top: 15px; background: rgba(0,0,0,0.2); padding: 10px; border-radius: 8px;">
                            <h5 style="margin-bottom: 8px; color: var(--text);">Workload/IO at exact time of crash:</h5>
                            <ul style="list-style: none; padding-left: 0; font-size: 0.9em; display: grid; grid-template-columns: 1fr 1fr; gap: 8px;">
                                <li><strong>CPU:</strong> ${rootCause.evidence.cpu_pct_of_limit ? rootCause.evidence.cpu_pct_of_limit.toFixed(2) : 0}% of limit</li>
                                <li><strong>Memory:</strong> ${rootCause.evidence.mem_pct_of_limit ? rootCause.evidence.mem_pct_of_limit.toFixed(2) : 0}% of limit</li>
                                <li><strong>Net RX:</strong> ${rootCause.evidence.net_rx_bytes_per_sec ? (rootCause.evidence.net_rx_bytes_per_sec / 1048576).toFixed(2) : 0} MB/s</li>
                                <li><strong>Net TX:</strong> ${rootCause.evidence.net_tx_bytes_per_sec ? (rootCause.evidence.net_tx_bytes_per_sec / 1048576).toFixed(2) : 0} MB/s</li>
                                <li><strong>Disk Read:</strong> ${rootCause.evidence.blk_read_bytes_per_sec ? (rootCause.evidence.blk_read_bytes_per_sec / 1048576).toFixed(2) : 0} MB/s</li>
                                <li><strong>Disk Write:</strong> ${rootCause.evidence.blk_write_bytes_per_sec ? (rootCause.evidence.blk_write_bytes_per_sec / 1048576).toFixed(2) : 0} MB/s</li>
                            </ul>
                        </div>
                    ` : ''}
                </div>
            ` : `
                <div class="root-cause-card">
                    <h4>No root-cause reason yet</h4>
                    <p>This pod hasn't triggered an RCA evaluation (no recent restart/pending/eviction/NotReady incident). Reasons appear here automatically the moment one is detected.</p>
                </div>
            `}
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
            <div style="margin-top: 20px;">
                <h4>Metrics Trend (Last <span id="trend-time-val">1h</span>)</h4>
                <div id="pod-history-chart" style="width: 100%; height: 260px; border-radius: 12px; padding: 12px; background: rgba(0,0,0,0.25); border: 1px solid var(--border); margin-top: 10px;"></div>
            </div>
        </div>
    `;
}

function renderPodHistoryChart() {
    if (window.podHistoryChartInstance) {
        window.podHistoryChartInstance.dispose();
        window.podHistoryChartInstance = null;
    }
    const chartEl = document.getElementById("pod-history-chart");
    if (!chartEl) return;

    const metrics = state.podMetricsHistory || [];
    const docker = state.podDockerHistory || [];

    const allTimes = [...new Set([
        ...metrics.map(m => m.timestamp),
        ...docker.map(d => d.timestamp)
    ])].sort();

    if (!allTimes.length) {
        chartEl.innerHTML = `<p style="color:var(--muted); text-align:center; padding-top:100px; margin:0;">No metrics history in this window.</p>`;
        return;
    }

    const cpuData = [];
    const memData = [];
    const netRxData = [];
    const netTxData = [];
    const diskRdData = [];
    const diskWrData = [];

    const metricsMap = new Map(metrics.map(m => [m.timestamp, m]));
    const dockerMap = new Map(docker.map(d => [d.timestamp, d]));

    allTimes.forEach(t => {
        const m = metricsMap.get(t);
        const d = dockerMap.get(t);

        cpuData.push(m ? m.cpu_pct_of_limit || 0 : null);
        memData.push(m ? m.mem_pct_of_limit || 0 : null);

        netRxData.push(d ? (d.net_rx_bytes_per_sec || 0) / 1_048_576 : null);
        netTxData.push(d ? (d.net_tx_bytes_per_sec || 0) / 1_048_576 : null);
        diskRdData.push(d ? (d.blk_read_bytes_per_sec || 0) / 1_048_576 : null);
        diskWrData.push(d ? (d.blk_write_bytes_per_sec || 0) / 1_048_576 : null);
    });

    const xLabels = allTimes.map(t => new Date(t).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }));

    window.podHistoryChartInstance = echarts.init(chartEl, null, { renderer: "canvas" });
    window.podHistoryChartInstance.setOption({
        backgroundColor: "transparent",
        textStyle: { color: "#e2e8f0" },
        grid: { top: 50, bottom: 40, left: 50, right: 50 },
        tooltip: {
            trigger: "axis",
            formatter: function(params) {
                let s = `<strong>${params[0].axisValue}</strong><br/>`;
                params.forEach(p => {
                    let val = p.value;
                    if (val === null || val === undefined) val = "-";
                    else if (p.seriesName.includes("%")) val = val.toFixed(1) + "%";
                    else val = val.toFixed(3) + " MB/s";
                    s += `${p.marker} ${p.seriesName}: ${val}<br/>`;
                });
                return s;
            }
        },
        legend: {
            data: ["CPU %", "Mem %", "Net RX", "Net TX", "Disk Read", "Disk Write"],
            textStyle: { color: "#94a3b8", fontSize: 9 }
        },
        xAxis: {
            type: "category",
            data: xLabels,
            axisLine: { lineStyle: { color: "rgba(148, 163, 184, 0.4)" } },
            axisLabel: { color: "#cbd5e1" },
        },
        yAxis: [
            {
                type: "value",
                name: "MB/s",
                position: "left",
                axisLine: { lineStyle: { color: "rgba(148, 163, 184, 0.4)" } },
                axisLabel: { color: "#cbd5e1" }
            },
            {
                type: "value",
                name: "% Limit",
                position: "right",
                axisLine: { lineStyle: { color: "rgba(148, 163, 184, 0.4)" } },
                axisLabel: { color: "#cbd5e1" },
                max: 100
            }
        ],
        series: [
            { name: "CPU %", type: "line", yAxisIndex: 1, data: cpuData, itemStyle: { color: "#38bdf8" }, symbol: "none" },
            { name: "Mem %", type: "line", yAxisIndex: 1, data: memData, itemStyle: { color: "#facc15" }, symbol: "none" },
            { name: "Net RX", type: "line", data: netRxData, itemStyle: { color: "#34d399" }, symbol: "none" },
            { name: "Net TX", type: "line", data: netTxData, itemStyle: { color: "#a78bfa" }, symbol: "none" },
            { name: "Disk Read", type: "line", data: diskRdData, itemStyle: { color: "#fb7185" }, symbol: "none" },
            { name: "Disk Write", type: "line", data: diskWrData, itemStyle: { color: "#f43f5e" }, symbol: "none" }
        ]
    });
}

async function loadPodDetails(uid) {
    state.selectedPodUid = uid;
    renderTree(state.tree);
    renderPodDetails();
    
    const timeSelect = document.getElementById("filter-time");
    const minutes = timeSelect ? timeSelect.value : 60;
    
    const [details, metricsHistory, dockerHistory] = await Promise.all([
        fetchJson(`/api/pods/${encodeURIComponent(uid)}`),
        fetchJson(`/api/pods/${encodeURIComponent(uid)}/metrics?minutes=${minutes}`),
        fetchJson(`/api/pods/${encodeURIComponent(uid)}/docker?minutes=${minutes}`)
    ]);
    
    if (!details) return;
    state.podDetails = details;
    state.podMetricsHistory = metricsHistory || [];
    state.podDockerHistory = dockerHistory || [];
    
    renderPodDetails();
    
    const trendTimeValSpan = document.getElementById("trend-time-val");
    if (trendTimeValSpan && timeSelect) {
        trendTimeValSpan.textContent = timeSelect.options[timeSelect.selectedIndex].text.replace("Last ", "");
    }
    
    renderPodHistoryChart();
}

function setupTreeSelection() {
    const container = document.getElementById("cluster-tree");
    if (!container) return;
    container.addEventListener("click", event => {
        // 1. Select and load pod details if a pod row is clicked
        const podRow = event.target.closest(".tree-pod-row");
        if (podRow) {
            const uid = podRow.dataset.podUid;
            if (!uid) return;
            if (state.selectedPodUid === uid) return;
            loadPodDetails(uid);
            return;
        }

        // 2. Toggle collapsed state if a header element (strong or span) is clicked
        const header = event.target.closest(".tree-node > strong, .tree-node > span");
        if (header) {
            const targetNode = header.parentElement;
            const key = targetNode.dataset.treeKey;
            if (!key) return;

            const isCollapsed = targetNode.classList.toggle("collapsed");

            // Nodes default to open; namespaces/deployments default to closed
            if (key.startsWith("node:")) {
                if (isCollapsed) {
                    state.expandedNodes.add(key); // In set = collapsed
                } else {
                    state.expandedNodes.delete(key);
                }
            } else {
                if (!isCollapsed) {
                    state.expandedNodes.add(key); // In set = expanded
                } else {
                    state.expandedNodes.delete(key);
                }
            }
        }
    });
}

function setupFailuresSelection() {
    const container = document.getElementById("failures-list");
    if (!container) return;
    container.addEventListener("click", event => {
        const row = event.target.closest(".failure-row");
        if (!row) return;
        const uid = row.dataset.podUid;
        if (!uid) return;
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

async function loadFailures() {
    const data = await fetchJson("/api/pods/failing");
    if (data) {
        state.failures = data;
        renderFailures();
    }
}

async function loadCollectorHealth() {
    const data = await fetchJson("/api/system/health");
    if (data) {
        state.collectorHealth = data;
        renderCollectorHealth();
    }
}

function updatePaginationUI(prefix, currentPage, totalPages) {
    const prevBtn = document.getElementById(`${prefix}-prev`);
    const nextBtn = document.getElementById(`${prefix}-next`);
    const numSpan = document.getElementById(`${prefix}-page-num`);
    
    if (prevBtn) {
        prevBtn.disabled = currentPage <= 1;
    }
    if (nextBtn) {
        nextBtn.disabled = currentPage >= totalPages;
    }
    if (numSpan) {
        numSpan.textContent = `Page ${currentPage} of ${totalPages || 1}`;
    }
}

async function loadAlertsPage(page) {
    state.alertsPage = Math.max(1, page);
    const data = await fetchJson(`/api/alerts?page=${state.alertsPage}&page_size=10`);
    if (data) {
        state.alerts = data.items || data;
        state.alertsPages = data.pages || 1;
        renderAlerts();
        updatePaginationUI("alerts", state.alertsPage, state.alertsPages);
    }
}

async function loadTimelinePage(page) {
    state.timelinePage = Math.max(1, page);
    const timeSelect = document.getElementById("filter-time");
    const minutes = timeSelect ? timeSelect.value : 60;
    const data = await fetchJson(`/api/events/timeline?minutes=${minutes}&page=${state.timelinePage}&page_size=10`);
    if (data) {
        state.timeline = data.items || data;
        state.timelinePages = data.pages || 1;
        renderTimeline();
        updatePaginationUI("timeline", state.timelinePage, state.timelinePages);
    }
}

async function refreshData() {
    const timeSelect = document.getElementById("filter-time");
    const minutes = timeSelect ? timeSelect.value : 60;
    const results = await Promise.allSettled([
        fetchJson("/api/cluster/summary"),
        fetchJson("/api/cluster/tree"),
        fetchJson("/api/alerts?limit=20"),
        fetchJson(`/api/events/timeline?minutes=${minutes}`),
        fetchJson("/api/rankings/all"),
        fetchJson("/api/nodes/compare"),
        fetchJson("/api/cluster/resources"),
        fetchJson("/api/pods/failing"),
        fetchJson("/api/system/health"),
    ]);

    const [
        summaryResult, treeResult, alertsResult, timelineResult, rankingsResult,
        nodeCompareResult, clusterResourcesResult, failuresResult, healthResult,
    ] = results;

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
        state.alerts = alertsResult.value.items || alertsResult.value;
        state.alertsPages = alertsResult.value.pages || 1;
        renderAlerts();
        updatePaginationUI("alerts", state.alertsPage, state.alertsPages);
    } else if (alertsResult.status === "rejected") {
        console.error("Failed to load alerts", alertsResult.reason);
    }

    if (timelineResult.status === "fulfilled" && timelineResult.value) {
        state.timeline = timelineResult.value.items || timelineResult.value;
        state.timelinePages = timelineResult.value.pages || 1;
        renderTimeline();
        updatePaginationUI("timeline", state.timelinePage, state.timelinePages);
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
        renderNodeTable();
    } else if (nodeCompareResult.status === "rejected") {
        console.error("Failed to load node comparisons", nodeCompareResult.reason);
    }

    if (clusterResourcesResult.status === "fulfilled" && clusterResourcesResult.value) {
        state.clusterResources = clusterResourcesResult.value;
        renderClusterResources();
    } else if (clusterResourcesResult.status === "rejected") {
        console.error("Failed to load cluster resources", clusterResourcesResult.reason);
    }

    if (failuresResult.status === "fulfilled" && failuresResult.value) {
        state.failures = failuresResult.value;
        renderFailures();
    } else if (failuresResult.status === "rejected") {
        console.error("Failed to load pod failures", failuresResult.reason);
    }

    if (healthResult.status === "fulfilled" && healthResult.value) {
        state.collectorHealth = healthResult.value;
        renderCollectorHealth();
    } else if (healthResult.status === "rejected") {
        console.error("Failed to load collector health", healthResult.reason);
    }

    setText("last-updated", `updated: ${new Date().toLocaleTimeString()}`);
}

// --- NEW: toast notifications for instant pod-failure reasons ----------
function showToast({ title, message, severity = "critical" }) {
    const container = document.getElementById("toast-container");
    if (!container) return;

    const toast = document.createElement("div");
    toast.className = `toast severity-${severity}`;
    toast.innerHTML = `
        <button class="toast-close" aria-label="Dismiss">×</button>
        <strong>${title}</strong>
        <p>${message}</p>
    `;
    toast.querySelector(".toast-close").addEventListener("click", () => toast.remove());
    container.appendChild(toast);

    // Keep alert on screen for 2 minutes to allow user to read it
    setTimeout(() => toast.remove(), 120000);
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
                loadAlertsPage(state.alertsPage);
                loadTimelinePage(state.timelinePage);

                // NEW: instant "why did my pod fail?" toast, straight from the
                // RCA verdict that came with this alert.
                const rca = payload.rca || {};
                const podName = rca.pod_name || payload.alert.pod_name || "A pod";
                const reason = rca.short_reason || payload.alert.title || "New incident detected";
                const explanation = rca.explanation || payload.alert.message || "";
                showToast({
                    title: `${podName} — ${reason}`,
                    message: explanation,
                    severity: payload.alert.severity || rca.severity || "warning",
                });

                // Refresh the dedicated failures panel + node/cluster resource
                // cards so they reflect the new incident right away too.
                loadFailures();
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

function setupPaginationEvents() {
    const prevAlerts = document.getElementById("alerts-prev");
    const nextAlerts = document.getElementById("alerts-next");
    const prevTimeline = document.getElementById("timeline-prev");
    const nextTimeline = document.getElementById("timeline-next");

    if (prevAlerts) {
        prevAlerts.addEventListener("click", () => {
            if (state.alertsPage > 1) loadAlertsPage(state.alertsPage - 1);
        });
    }
    if (nextAlerts) {
        nextAlerts.addEventListener("click", () => {
            if (state.alertsPage < state.alertsPages) loadAlertsPage(state.alertsPage + 1);
        });
    }
    if (prevTimeline) {
        prevTimeline.addEventListener("click", () => {
            if (state.timelinePage > 1) loadTimelinePage(state.timelinePage - 1);
        });
    }
    if (nextTimeline) {
        nextTimeline.addEventListener("click", () => {
            if (state.timelinePage < state.timelinePages) loadTimelinePage(state.timelinePage + 1);
        });
    }
}

function setupCollapsibleHeaders() {
    document.querySelectorAll(".subpanel-header, .panel-header").forEach(header => {
        header.style.cursor = "pointer";
        header.style.userSelect = "none";

        const titleEl = header.querySelector("h2, h3");
        if (titleEl && !titleEl.querySelector(".header-chevron")) {
            titleEl.style.position = "relative";
            titleEl.style.paddingLeft = "20px";
            
            const arrow = document.createElement("span");
            arrow.className = "header-chevron";
            arrow.textContent = "▼";
            arrow.style.position = "absolute";
            arrow.style.left = "0";
            arrow.style.top = "2px";
            arrow.style.fontSize = "0.7rem";
            arrow.style.color = "var(--muted)";
            arrow.style.transition = "transform 0.2s ease";
            titleEl.appendChild(arrow);
        }

        header.addEventListener("click", event => {
            if (event.target.closest(".pagination-controls") || event.target.closest("button")) {
                return;
            }
            const content = header.nextElementSibling;
            if (content) {
                const isCollapsed = content.classList.toggle("collapsed-content");
                const pag = header.querySelector(".pagination-controls");
                if (pag) {
                    pag.style.display = isCollapsed ? "none" : "";
                }
                const arrow = header.querySelector(".header-chevron");
                if (arrow) {
                    arrow.style.transform = isCollapsed ? "rotate(-90deg)" : "rotate(0deg)";
                }
            }
        });
    });
}

window.addEventListener("load", async () => {
    initCharts();
    setupTreeSelection();
    setupFailuresSelection();
    setupPaginationEvents();
    setupCollapsibleHeaders();
    await refreshData();
    renderPodDetails();
    setupWebSocket();
    setInterval(refreshData, 30_000);
});
