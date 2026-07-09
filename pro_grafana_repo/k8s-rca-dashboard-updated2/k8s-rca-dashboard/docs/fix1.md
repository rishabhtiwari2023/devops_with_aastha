1. Fix kube-state-metrics (Add Skip TLS Verification)
You can run this single command to append the insecure-skip-tls-verify flag directly to the deployment:

bash
kubectl patch deployment kube-state-metrics -n monitoring --type='json' -p='[{"op": "add", "path": "/spec/template/spec/containers/0/args/-", "value": "--insecure-skip-tls-verify=true"}]'
(Alternatively, if you prefer to edit it manually in your text editor):

bash
kubectl edit deployment kube-state-metrics -n monitoring
Find the containers: section, look for the first container's args: list, and add the line:

yaml
- --insecure-skip-tls-verify=true
2. Fix node-exporter (Listen on all interfaces on port 9100)
Because DaemonSets mount host paths (which differ depending on your setup), it is safest to modify this using kubectl edit to avoid overwriting other arguments:

bash
kubectl edit daemonset node-exporter -n monitoring
Look for the args: list under the container spec.
Find the line that looks like:
yaml
- --web.listen-address=127.0.0.1:9101
Change it to:
yaml
- --web.listen-address=0.0.0.0:9100
Save and exit the editor.
(Note: If the DaemonSet is named differently, you can find the name using kubectl get daemonsets -n monitoring first).

1:47 PM
1:49 PM
I will que


python -c "import base64; print(base64.b64decode('LS0tLS1CRUdJTiBDRVJUSUZJQ0FURS0tLS0tCk1JSURCVENDQWUyZ0F3SUJBZ0lJUnBibkZhNDg2bGt3RFFZSktvWklodmNOQVFFTEJRQXdGVEVUTUJFR0ExVUUKQXhNS2EzVmlaWEp1WlhSbGN6QWVGdzB5TmpBM01EZ3dPRFF3TkRsYUZ3MHpOakEzTURVd09EUTFORGxhTUJVeApFekFSQmdOVkJBTVRDbXQxWW1WeWJtVjBaWE13Z2dFaU1BMEdDU3FHU0liM0RRRUJBUVVBQTRJQkR3QXdnZ0VLCkFvSUJBUURqU2NmR1hydXNrSlBSN2ZoSEZzMVVUZ1FBT1pOcGNsK3dST2FINGRWaEIwQVQzNDBkRE5WbG9RVy8KNU1qSEVDZFFHZDJ5bTdGd3FPYTcwZnM1d2x5dHJhdWNyem00UlBxVHJFaEtBMWNKTVhFdDllRnBYWTlReHl1RApRTmVrR2RValRZZWd5NDNHb1VBbEVhZ3VYVk81LyswWjRFcWc2dXJ1b2IrTHd3NmI1UUlWWXc0Yy9nTXBmM2hyCit1MWtSQ3k0WHNWalk4QldtQWUwdGVWeWtHK1U0Nk02a0o0eWgwUXBFSU1hbXZhYThEaVlxS3dDdENtUG5jVUMKeUMvWmxTMDFORWZ4ZWpoNEZPcmMwSDVDakxBSENjYU4valR5bXNUb3pXWElpd2JvT2ZoRjJzNmNPdXY5cU40VApBbGhLZGNReDh6aUM4V2FoMjdZTEhaVGJrcktkQWdNQkFBR2pXVEJYTUE0R0ExVWREd0VCL3dRRUF3SUNwREFQCkJnTlZIUk1CQWY4RUJUQURBUUgvTUIwR0ExVWREZ2NXQklk').decode('utf-8', errors='ignore'))"