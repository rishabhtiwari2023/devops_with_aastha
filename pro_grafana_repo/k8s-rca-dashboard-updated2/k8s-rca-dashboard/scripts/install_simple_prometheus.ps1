$ErrorActionPreference = 'Stop'

Write-Host "Installing a lightweight Prometheus instance into monitoring namespace..." -ForegroundColor Cyan
kubectl create namespace monitoring --dry-run=client -o yaml | kubectl apply -f -

$promYaml = @'
apiVersion: v1
kind: ConfigMap
metadata:
  name: prometheus-config
  namespace: monitoring
data:
  prometheus.yml: |
    global:
      scrape_interval: 15s
      evaluation_interval: 15s
    scrape_configs:
      - job_name: prometheus
        static_configs:
          - targets: ['localhost:9090']
      - job_name: kube-state-metrics
        static_configs:
          - targets: ['kube-state-metrics.monitoring.svc.cluster.local:8080']
      - job_name: node-exporter
        static_configs:
          - targets: ['node-exporter.monitoring.svc.cluster.local:9100']
'@

$promYaml | kubectl apply -f -

$deployYaml = @'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: simple-prometheus
  namespace: monitoring
  labels:
    app: simple-prometheus
spec:
  replicas: 1
  selector:
    matchLabels:
      app: simple-prometheus
  template:
    metadata:
      labels:
        app: simple-prometheus
        app.kubernetes.io/component: prometheus
        app.kubernetes.io/instance: k8s
        app.kubernetes.io/name: prometheus
        app.kubernetes.io/part-of: kube-prometheus
    spec:
      containers:
        - name: prometheus
          image: prom/prometheus:v2.53.3
          args:
            - "--config.file=/etc/prometheus/prometheus.yml"
            - "--storage.tsdb.path=/prometheus"
            - "--web.console.libraries=/usr/share/prometheus/console_libraries"
            - "--web.console.templates=/usr/share/prometheus/consoles"
            - "--web.enable-lifecycle"
          ports:
            - containerPort: 9090
              name: web
          volumeMounts:
            - name: config
              mountPath: /etc/prometheus
            - name: storage
              mountPath: /prometheus
      volumes:
        - name: config
          configMap:
            name: prometheus-config
        - name: storage
          emptyDir: {}
'@

$deployYaml | kubectl apply -f -

$serviceYaml = @'
apiVersion: v1
kind: Service
metadata:
  name: simple-prometheus
  namespace: monitoring
spec:
  selector:
    app: simple-prometheus
  ports:
    - port: 9090
      targetPort: web
      protocol: TCP
      name: web
'@

$serviceYaml | kubectl apply -f -

Write-Host "Waiting for simple Prometheus deployment to be ready..." -ForegroundColor Cyan
kubectl rollout status deployment/simple-prometheus -n monitoring --timeout=300s

Write-Host "Simple Prometheus installed and service created." -ForegroundColor Green
Write-Host "Forward it with .\scripts\forward_prometheus.ps1" -ForegroundColor Green
