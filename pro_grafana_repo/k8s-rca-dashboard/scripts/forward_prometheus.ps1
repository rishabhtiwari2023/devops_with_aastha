$ErrorActionPreference = 'Stop'

Write-Host "Forwarding Prometheus to localhost:9090..." -ForegroundColor Cyan
$service = kubectl get svc simple-prometheus -n monitoring --ignore-not-found -o name
if ($service) {
    Write-Host "Using simple-prometheus service..." -ForegroundColor Cyan
    kubectl port-forward -n monitoring svc/simple-prometheus 9090:9090
} else {
    Write-Host "simple-prometheus service not found, falling back to deployment port-forward..." -ForegroundColor Yellow
    kubectl port-forward -n monitoring deployment/simple-prometheus 9090:9090
}
