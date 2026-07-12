# k3s

Lightweight Kubernetes cluster for the homelab.

## Topology

| Node | Runs on | Role |
|---|---|---|
| `k3s-master` | Proxmox CT/VM on `server` | control plane |
| `node-1`, `node-2` | Raspberry Pi | workers |
| Proxmox CTs | `server` | workers |
| `shield` | Dell laptop | worker (joins later) |

`node-3` (Pi 5) is dedicated to openclaw and stays out of the cluster.

Mixed-architecture cluster: amd64 (Proxmox CTs, shield) + arm64 (Pis). All
container images must be multi-arch, or workloads pinned with
`nodeSelector: kubernetes.io/arch`.

## Install

All hosts must resolve as `<hostname>.rps-home.com` (A records in Pi-hole)
before installing.

1. On the control-plane node:

   ```sh
   ./server.sh
   ```

   Prints the join token at the end. Keeps k3s defaults: traefik ingress,
   servicelb, and the Rancher `local-path` StorageClass (used for all PVs
   for now).

2. On each worker:

   ```sh
   K3S_TOKEN=<token> ./agent.sh              # joins k3s-master.rps-home.com
   K3S_TOKEN=<token> ./agent.sh other.rps-home.com   # or a different server
   ```

## After install

Cluster addons and applications are NOT installed by hand — ArgoCD manages
them from `cluster/`. See [cluster/bootstrap/](../../cluster/bootstrap/README.md).
