#!/bin/bash
# realtime_pod_workload.sh
# This script uses pure docker commands to map containers back to Kubernetes Pods 
# and displays their live Workload / IO statistics in real-time.

# If the user passes --loop, it will run continuously using `watch`.
if [ "$1" == "--loop" ]; then
    # Clear screen and run in a loop every 2 seconds
    watch -n 2 -t -c "$0"
    exit 0
fi

# Print Header
printf "%-25s | %-45s | %-25s | %-8s | %-18s | %-22s | %-22s\n" "NAMESPACE" "POD NAME" "CONTAINER NAME" "CPU %" "MEMORY" "NET I/O" "BLOCK I/O"
printf "%s\n" "--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------"

# Run docker stats once for all containers (no-stream)
# Format: ContainerID | CPU | MemUsage | NetIO | BlockIO
docker stats --no-stream --format '{{.ID}}|{{.CPUPerc}}|{{.MemUsage}}|{{.NetIO}}|{{.BlockIO}}' | while IFS='|' read -r id cpu mem net block; do
    
    # Extract K8s labels using docker inspect
    # These labels map the exact docker container to the exact kubernetes pod and namespace.
    labels=$(docker inspect --format '{{index .Config.Labels "io.kubernetes.pod.namespace"}}|{{index .Config.Labels "io.kubernetes.pod.name"}}|{{index .Config.Labels "io.kubernetes.container.name"}}' "$id" 2>/dev/null)
    
    namespace=$(echo "$labels" | cut -d'|' -f1)
    pod_name=$(echo "$labels" | cut -d'|' -f2)
    container_name=$(echo "$labels" | cut -d'|' -f3)

    # Only show kubernetes pods (skip infrastructure containers with no k8s labels)
    if [ -n "$namespace" ] && [ -n "$pod_name" ] && [ "$namespace" != "<no value>" ]; then
        
        # Format the pause container clearly
        if [ "$container_name" == "POD" ]; then
            container_name="[k8s-pause-network]"
        fi
        
        printf "%-25s | %-45s | %-25s | %-8s | %-18s | %-22s | %-22s\n" "$namespace" "$pod_name" "$container_name" "$cpu" "$mem" "$net" "$block"
    fi
done | sort -k1,1 -k2,2
