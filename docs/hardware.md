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

**Capacity was only half the problem.** The default `local-path` StorageClass
binds a PersistentVolume to whichever node first scheduled its pod, and that pod
can then never move: it cannot be rescheduled when the node goes down, and
`homelab nuke` erases the data outright. A bigger card raises the ceiling and
changes none of that.

So there are two StorageClasses, chosen deliberately per workload:

| Class | Backed by | Use for |
|---|---|---|
| `local-path` (default) | node-local disk | workloads that replicate themselves (PostgreSQL), or where loss is acceptable |
| `nfs` | `portal:/export/kubernetes-nfs-storage` | anything that must survive a rebuild or move between nodes |

`nfs` uses `reclaimPolicy: Retain` precisely so that a cluster rebuild cannot
destroy it.

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
every workload on the `nfs` class. That is an accepted trade — it buys
node-independence and rebuild-survival — but it means OpenBao and PostgreSQL
deliberately stay on `local-path` so the secret store and the database do not
go down with the NAS.

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
