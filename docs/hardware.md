# Hardware

Everything in the lab sits on the `192.168.11.0/24` subnet, behind the Gateway
Pi at `192.168.11.1` which routes to the house network on `192.168.0.0/24`.

## k3s cluster

| Node | IP | Role | Machine |
|---|---|---|---|
| `k3s-server` | 192.168.11.7 | control plane | Raspberry Pi, 16 GB |
| `k3s-worker-1` | 192.168.11.6 | worker | Raspberry Pi, 16 GB |
| `k3s-worker-2` | 192.168.11.5 | worker | Raspberry Pi, 16 GB |

All three run Ubuntu 26.04, **arm64**. There is no amd64 node — every image
deployed here must have a `linux/arm64` manifest, which CI enforces.

The machine-readable version of this table is
[`clusters/rps/cluster.yaml`](../clusters/rps/cluster.yaml). That file is the
input to the CLI; this page is for humans. If they disagree, the YAML wins.

### Storage

RAM is uniform and generous (3 x 16 GB, typically under 15% used).

Disk was previously lopsided — `k3s-worker-2` had ~57 Gi against ~115 Gi on the
other two. A 256 GB card has since been fitted, and the cluster confirms it:

| Node | Free |
|---|---|
| `k3s-server` | ~104 Gi |
| `k3s-worker-1` | ~108 Gi |
| `k3s-worker-2` | **~222 Gi** |

`k3s-worker-2` is now the *largest* node, which inverts the old constraint —
prefer it for anything storage-hungry rather than avoiding it.

**Capacity was only half the problem, and the smaller half.** The old default
`local-path` StorageClass bound a PersistentVolume to whichever node first
scheduled its pod, and that pod could then never move: it could not be
rescheduled when the node went down, and `homelab nuke` erased the data
outright. A bigger card raises the ceiling and changes none of that — which is
why upgrading worker-2 solved the capacity complaint without solving the real
one.

So there is now exactly **one** StorageClass, and it is the default:

| Class | Backed by | Notes |
|---|---|---|
| `nfs` (default, only) | `portal:/export/kubernetes-nfs-storage` | `reclaimPolicy: Retain`, RWX-capable, `hard` mounts |

`local-path` is gone: k3s runs with `--disable=local-storage`. A PVC that names
no `storageClassName` lands on `nfs`, which is what makes it the default for
applications without any per-app configuration.

`reclaimPolicy: Retain` exists precisely so that a cluster rebuild cannot
destroy the data. The trade this makes — NFS under PostgreSQL, Prometheus and
OpenBao, all of which would rather have local `fsync` — is argued in full in
[ADR 0006](decisions/0006-nfs-default-storage.md). Read it before moving
anything back.

## Other hosts

| Host | IP | Role |
|---|---|---|
| `portal` | 192.168.11.3 | OpenMediaVault NAS — NFS (2049) and a web UI on 80 |
| `Gateway` | 192.168.11.1 | Routes 192.168.11.0/24 to 192.168.0.0/24; DHCP for the lab |

Neither is part of the cluster. `portal` exports **`/export/kubernetes-nfs-storage`**, which backs the `nfs`
StorageClass via `infra/services/nfs-provisioner/`.

> **The export ACL currently allows `192.168.0.0/24` only**, which is the house
> network — not `192.168.11.0/24`, where the cluster lives. Mounts therefore fail
> with `access denied by server`. Fix it in the OpenMediaVault UI
> (Services → NFS → Shares); OMV regenerates `/etc/exports` from its own
> database, so editing that file by hand is overwritten on the next save.

Nodes also need the `nfs-common` package for `/sbin/mount.nfs` to exist. That is
installed by `homelab install`, not by hand — without it a pod hangs in
`ContainerCreating` reporting only `exit status 32`, which says nothing about the
real cause.

Because `portal` is a single un-replicated NAS, it is a hard dependency for
**every** stateful workload in the cluster — OpenBao and PostgreSQL included,
which previously stayed on `local-path` for exactly this reason. That is the
central cost of [ADR 0006](decisions/0006-nfs-default-storage.md), and it is
accepted rather than solved: it buys node-independence and rebuild-survival, and
it concentrates the blast radius of a NAS outage from one dashboard to all
cluster state.

The `hard` mount option means the failure mode is a hang, not corruption. Pods
block in uninterruptible sleep until `portal` returns, then continue. So a NAS
outage looks like a cluster-wide freeze of stateful services rather than data
loss — annoying, recoverable, and worth recognising quickly. **If several
unrelated stateful pods go unready at once, check `portal` before anything
else.**

`portal` is a 4GB Pi, and it is now the single most important machine in the lab
after the control plane. It has no redundancy and no backup target of its own,
which makes off-NAS backups the highest-value outstanding work here.

The Gateway Pi is a single point of failure for the entire lab subnet and is not
yet under configuration management.

## Network

- Lab subnet: `192.168.11.0/24`, gateway `192.168.11.1`
- MetalLB pool: `192.168.11.200-192.168.11.250` — must stay outside the
  Gateway's DHCP scope
- Traefik ingress VIP: `192.168.11.240`, pinned so it survives a rebuild
- Tailnet: `mongoose-galaxy.ts.net` — how anything is reached from outside the LAN

There is no local DNS server. Hosts are addressed by IP on the LAN and by
MagicDNS name on the tailnet.
