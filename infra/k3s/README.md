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

## Networking choices

- **CNI: Calico** (flannel and k3s network policy disabled). Installed by
  `server.sh` via the Tigera operator + `calico-installation.yaml` — it can't
  be GitOps-managed because no pod (including ArgoCD) starts without a CNI.
- **LoadBalancer: MetalLB** (k3s servicelb disabled). Managed by ArgoCD:
  `cluster/infrastructure/metallb*.yaml`.
- **Ingress: traefik** (k3s default, kept).
- **Storage: local-path** (k3s default, kept).

## Install

All hosts must resolve as `<hostname>.rps-home.com` (A records in Pi-hole)
before installing.

1. On the control-plane node:

   ```sh
   ./server.sh
   ```

   Installs the k3s server, then Calico, waits for the node to go Ready,
   and prints the join token.

2. On each worker:

   ```sh
   K3S_TOKEN=<token> ./k3s-worker-agent.sh            # joins k3s-master.rps-home.com
   K3S_TOKEN=<token> ./k3s-worker-agent.sh other.rps-home.com
   ```

## After install

Cluster addons and applications are NOT installed by hand — ArgoCD manages
them from `cluster/`. See [cluster/bootstrap/](../../cluster/bootstrap/README.md).
