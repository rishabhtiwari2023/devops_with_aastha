$ErrorActionPreference = 'Stop'

Write-Host "Forwarding Longhorn frontend to localhost:8080..." -ForegroundColor Cyan
kubectl port-forward -n longhorn-system svc/longhorn-frontend 8080:80
