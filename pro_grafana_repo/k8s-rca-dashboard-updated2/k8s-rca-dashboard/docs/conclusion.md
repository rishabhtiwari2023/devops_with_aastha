# Conclusion

Raptor Mini is primarily a **monitoring and Root Cause Analysis (RCA)** application. During its normal operation, it continuously **reads** data from Kubernetes, Prometheus, Docker, and Longhorn to collect the current cluster state and metrics.

The application **does not perform any write operations on the Kubernetes cluster or managed workloads**. It does **not**:

- Create Kubernetes resources
- Update Deployments, Pods, or Services
- Delete Kubernetes resources
- Scale workloads
- Restart Pods
- Modify Longhorn volumes
- Execute commands inside containers

The only write operations performed by the application are **internal**:

- Stores collected cluster information in its local SQLite database.
- Inserts historical metrics and event records.
- Generates and stores Root Cause Analysis (RCA) records.
- Creates and updates alert records (acknowledge/resolve).
- Periodically deletes old records from SQLite based on the retention policy.

Therefore, the application is effectively **read-only with respect to the Kubernetes cluster and infrastructure**. It observes cluster state, performs deterministic analysis, and exposes the results through REST APIs, WebSocket notifications, and the dashboard without modifying the cluster itself.

## Deployment Recommendation

Since the application only reads cluster resources and maintains its own internal SQLite database, it is generally suitable for deployment in a **QA environment** for validation and testing before production rollout.

Before deployment, verify that:

- The Kubernetes ServiceAccount has only the minimum required **read (get, list, watch)** permissions.
- Prometheus, Docker, and Longhorn endpoints are accessible.
- Persistent storage is available for the SQLite database (if historical data should survive pod restarts).
- Network policies allow access to the required monitoring endpoints.

If these prerequisites are met, deploying Raptor Mini to the QA server is an appropriate next step to validate its monitoring and RCA capabilities without impacting running workloads.