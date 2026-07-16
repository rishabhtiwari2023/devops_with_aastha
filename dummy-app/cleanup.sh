#!/bin/bash
# cleanup.sh
echo "Deleting dummy microservices architecture..."
kubectl delete namespace dummy-app
echo "Cleanup complete!"
