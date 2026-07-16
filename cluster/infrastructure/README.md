# Infrastructure (cluster addons)

One ArgoCD `Application` manifest per addon, at this directory's top level.
Subdirectories hold raw manifests owned by a companion `*-config`/`*-issuers`
Application (the root app does not recurse into them).

| Addon | Status | Purpose |
|---|---|---|
| `metallb.yaml` + `metallb-config.yaml` | ✅ | LoadBalancer IPs on the LAN (k3s servicelb is disabled). TODO: set the real address pool in `metallb-config/` |
| `cert-manager.yaml` + `cert-manager-issuers.yaml` | ✅ | TLS certificates via ACME against the step-ca LXC (`infra/step-ca/`). TODO: caBundle + CA hostname in `cert-manager-issuers/` |
| `kube-prometheus-stack.yaml` | ✅ | Prometheus + Grafana + Alertmanager (NodePorts 30000–30002, `local-path` storage) |
| CNI (Calico) | — | installed by `infra/k3s/server.sh`, not ArgoCD (chicken-and-egg) |
| storage | — | k3s built-in Rancher `local-path` StorageClass; nothing to install |
| ingress | — | traefik ships with k3s; gets its external IP from MetalLB |
