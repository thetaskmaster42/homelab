# Network

- **Domain:** `rps-home.com`
- **DNS:** Pi-hole — local DNS records for every host below
- **DHCP:** Pi-hole — reservations for all permanent hosts

## IP plan

> TODO: fill in actual subnet, gateway, and per-host reservations.

| Hostname | IP | Device | Notes |
|---|---|---|---|
| `pihole.rps-home.com` | TBD | Raspberry Pi | DNS + DHCP |
| `portal.rps-home.com` | TBD | Raspberry Pi | OpenMediaVault, NFS |
| `proxmox.rps-home.com` | TBD | HP desktop | Proxmox VE |
| `k3s-master.rps-home.com` | TBD | Proxmox CT | k3s control plane |
| TBD | TBD | Raspberry Pi ×2 | k3s workers |
| TBD | TBD | Raspberry Pi 5 | openclaw (dedicated) |
| TBD | TBD | Dell laptop | future k3s worker |

## Conventions

- Every permanent host gets a DHCP reservation and an A record in Pi-hole.
- Cluster ingress will use a wildcard-style scheme: `<app>.rps-home.com`
  pointed at the ingress/load-balancer IP (one Pi-hole record per app, or a
  dnsmasq wildcard entry).
