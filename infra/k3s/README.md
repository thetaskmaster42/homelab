# k3s

Lightweight Kubernetes cluster for the homelab.

## Topology

| Node | Runs on | Role |
|---|---|---|
| `k3s-master` | Proxmox CT/VM | control plane (server) |
| workers | Proxmox CTs + Raspberry Pis | agents |
| Dell laptop | bare metal | agent (joins later) |

Mixed architecture cluster: amd64 (Proxmox CTs, Dell) + arm64 (Pis). All
container images must be multi-arch, or workloads pinned with
`nodeSelector: kubernetes.io/arch`.

## Install

> TODO: rewrite install scripts for this topology (the old ones are in
> `archive/k3s-v1/`). Planned approach:
>
> 1. `server.sh` — install k3s server on the control-plane node
>    (`--tls-san k3s-master.rps-home.com`, traefik/servicelb decisions TBD)
> 2. `agent.sh` — join script taking the server URL + token
> 3. Node inventory driven by DNS names from Pi-hole, not hardcoded IPs

## After install

Cluster addons and applications are NOT installed by hand — ArgoCD manages
them from `cluster/`. See [cluster/bootstrap/](../../cluster/bootstrap/README.md).
