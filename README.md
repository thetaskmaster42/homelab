# Homelab

Infrastructure-as-code for the `rps-home.com` homelab: network services, a k3s
cluster, and the applications that run on it (GitOps via ArgoCD).

## Hardware

| Host | Device | Role |
|---|---|---|
| (TBD) | HP desktop | Proxmox VE — hosts the k3s control-plane container + worker containers |
| portal | Raspberry Pi | OpenMediaVault — NFS / NAS |
| (TBD) | Raspberry Pi | Pi-hole — DNS + DHCP for the network |
| (TBD) | Raspberry Pi 5 | Dedicated to openclaw |
| (TBD) | Raspberry Pi ×2 | k3s workers |
| (TBD) | Dell laptop | Future k3s worker (currently out of network) |

> TODO: fill in hostnames and static IPs/DHCP reservations for each node.

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

- [ ] Document node inventory (hostnames, IPs, DHCP reservations in Pi-hole)
- [ ] Provision k3s: control-plane container on Proxmox, Pis + containers as workers
- [ ] Bootstrap ArgoCD (`cluster/bootstrap/`)
- [ ] Cluster addons: NFS storage class, ingress, cert-manager, monitoring
- [ ] Migrate/redeploy applications (observability stack, data stack, …)
- [ ] Join the Dell laptop as an additional worker
