# Infrastructure (cluster addons)

One ArgoCD `Application` manifest per addon. Synced automatically by the
`infrastructure` root app.

| Addon | Status | Purpose |
|---|---|---|
| `kube-prometheus-stack.yaml` | ✅ | Prometheus + Grafana + Alertmanager (NodePorts 30000–30002, `local-path` storage) |
| storage | — | k3s built-in Rancher `local-path` StorageClass; nothing to install |
| ingress | planned | traefik ships with k3s; `<app>.rps-home.com` routing config |
| `cert-manager` | planned | TLS for ingress hosts |
| `metallb` | maybe | LoadBalancer IPs on the LAN (k3s servicelb may be enough) |
