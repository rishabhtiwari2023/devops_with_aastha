param(
    [switch]$WithLonghorn
)

$ErrorActionPreference = 'Stop'

Write-Host "Installing Prometheus monitoring stack into the Kubernetes cluster..." -ForegroundColor Cyan
kubectl create namespace monitoring --dry-run=client -o yaml | kubectl apply -f -

Write-Host "Applying kube-prometheus CRDs (this may take a few minutes)..." -ForegroundColor Cyan
kubectl apply -f "https://raw.githubusercontent.com/prometheus-operator/kube-prometheus/main/manifests/setup"

Write-Host "Applying kube-prometheus stack (this may take a few minutes)..." -ForegroundColor Cyan
kubectl apply -k "github.com/prometheus-operator/kube-prometheus?ref=main"

Write-Host "Waiting for monitoring pods to become ready..." -ForegroundColor Cyan
kubectl wait --for=condition=ready pod --all -n monitoring --timeout=600s

Write-Host "Prometheus stack installed." -ForegroundColor Green
Write-Host "To forward Prometheus to localhost:9090 run:" -ForegroundColor Yellow
Write-Host "  kubectl port-forward -n monitoring svc/prometheus-operated 9090:9090"
Write-Host "Then keep that terminal open while you use the dashboard."

if ($WithLonghorn) {
    Write-Host "\nInstalling Longhorn into the cluster..." -ForegroundColor Cyan
    kubectl apply -f https://raw.githubusercontent.com/longhorn/longhorn/v1.6.0/deploy/longhorn.yaml

    Write-Host "Waiting for Longhorn pods to become ready..." -ForegroundColor Cyan
    kubectl wait --for=condition=ready pod --all -n longhorn-system --timeout=900s

    Write-Host "Longhorn installed." -ForegroundColor Green
    Write-Host "To forward the Longhorn API to localhost:8080 run:" -ForegroundColor Yellow
    Write-Host "  kubectl port-forward -n longhorn-system svc/longhorn-frontend 8080:80"
    Write-Host "Then set RCA_LONGHORN_API_URL=http://127.0.0.1:8080/v1 in your .env file."
}
