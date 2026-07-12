# Infrastructure (cluster addons)

One ArgoCD `Application` manifest per addon. Synced automatically by the
`infrastructure` root app.

Planned addons, roughly in install order:

| Addon | Purpose |
|---|---|
| `metallb` or k3s servicelb | LoadBalancer IPs on the LAN |
| ingress (traefik, ships with k3s) | `<app>.rps-home.com` routing |
| `cert-manager` | TLS for ingress hosts |
| `nfs-provisioner` | StorageClass backed by portal's NFS export |
| `kube-prometheus-stack` | Monitoring (port from `archive/Observability/`) |
