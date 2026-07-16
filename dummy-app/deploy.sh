#!/bin/bash
# deploy.sh
echo "Deploying dummy microservices architecture..."
kubectl apply -f 00-namespace.yaml
kubectl apply -f 01-frontend.yaml
kubectl apply -f 02-backend.yaml
kubectl apply -f 03-database.yaml
kubectl apply -f 04-worker.yaml
echo "Deployment complete! Run 'kubectl get all -n dummy-app' to view."
