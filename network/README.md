# Network

- **Domain:** `rps-home.com`
- **DNS:** Pi-hole — every host resolves as `<hostname>.rps-home.com`
- **DHCP:** Pi-hole — reservations for all permanent hosts

## Hosts

| Hostname | IP | Device | Notes |
|---|---|---|---|
| `pihole.rps-home.com` | TBD | Raspberry Pi | DNS + DHCP |
| `portal.rps-home.com` | TBD | Raspberry Pi | OpenMediaVault, NFS/NAS |
| `server.rps-home.com` | TBD | HP desktop | Proxmox VE |
| `k3s-master.rps-home.com` | TBD | Proxmox CT on `server` | k3s control plane |
| `step-ca.rps-home.com` | TBD | Proxmox LXC on `server` | private CA (smallstep) — TODO confirm hostname |
| `node-1.rps-home.com` | TBD | Raspberry Pi | k3s worker |
| `node-2.rps-home.com` | TBD | Raspberry Pi | k3s worker |
| `node-3.rps-home.com` | TBD | Raspberry Pi 5 | openclaw (dedicated, not in cluster) |
| `shield.rps-home.com` | TBD | Dell laptop | future k3s worker (out of network) |

> TODO: record subnet, gateway, and the DHCP reservation IPs.

## Conventions

- Every permanent host gets a DHCP reservation and an A record in Pi-hole.
- Cluster ingress will use `<app>.rps-home.com` pointed at the ingress IP
  (one Pi-hole record per app, or a dnsmasq wildcard entry).
