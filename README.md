# Homelab

Infrastructure-as-code for the `rps-home.com` homelab: network services, a k3s
cluster, and the applications that run on it (GitOps via ArgoCD).

## Hardware

| Host | Device | Role |
|------|--------|-------|
| server.rps-home.com | HP desktop | Proxmox VE — hosts the k3s control-plane container + worker containers |
| portal.rps-home.com | Raspberry Pi | OpenMediaVault — NFS / NAS |
| pihole.rps-home.com | Raspberry Pi | Pi-hole — DNS + DHCP for the network |
| node-3.rps-home.com | Raspberry Pi 5 | Dedicated to openclaw |
| node-1.rps-home.com, node-2.rps-home.com | Raspberry Pi ×2 | k3s workers |
| shield.rps-home.com | Dell laptop | Future k3s worker (currently out of network) |

## Network

- Domain: **rps-home.com**
- DNS + DHCP: **Pi-hole** (all cluster hostnames get local DNS records here)
- Shared storage: NFS exports from **portal** (OpenMediaVault)

See [network/](network/README.md) for details.

## Repository layout

```
network/          Network design, IP plan, DNS records
infra/            Machines and platform services (below Kubernetes)
  pihole/         DNS + DHCP
  proxmox/        Hypervisor, container/VM definitions
  nas/            portal — OpenMediaVault, NFS exports
  k3s/            Cluster install/join scripts and node config
cluster/          Everything running ON Kubernetes (GitOps source of truth)
  bootstrap/      ArgoCD install + root app-of-apps
  infrastructure/ Cluster addons: ingress, storage, cert-manager, monitoring…
  apps/           End-user applications
archive/          Pre-v2 content kept for reference (not maintained)
```

## Roadmap

- [x] Document node inventory (hostnames; IPs/DHCP reservations still TBD)
- [ ] Provision k3s: control-plane container on Proxmox, Pis + containers as workers
- [ ] Bootstrap ArgoCD (`cluster/bootstrap/`)
- [ ] Cluster addons: ingress, cert-manager, monitoring (storage: k3s built-in
      `local-path` for now, NFS from portal later if needed)
- [ ] Migrate/redeploy applications (observability stack, data stack, …)
- [ ] Join the Dell laptop as an additional worker
