# 0006 — NFS is the default and only StorageClass

**Status:** accepted, 2026-08-24

Supersedes the storage split described in `0001`-era comments, where `nfs` was
opt-in and `local-path` was the default.

## Context

The cluster had two StorageClasses:

| Class | Provisioner | Default | Used by |
|---|---|---|---|
| `local-path` | k3s built-in `local-storage` addon | yes | cloudnative-pg, kube-prometheus-stack, openbao, prep-tracker-db |
| `nfs` | `nfs-subdir-external-provisioner` against `portal` | no | rh-dashboard only |

The split encoded a real distinction — put fsync-sensitive workloads on local
disk, put archival data on the NAS — and every one of those placements carried a
comment defending itself.

Three things made it stop paying for itself:

1. **Node-pinning is not survivable.** A `local-path` PV binds to whichever node
   first scheduled its pod and can never move. When `k3s-worker-1` went
   `NotReady`, the pods holding those volumes could not be rescheduled anywhere.
   This is not a capacity problem and a bigger SD card does not fix it.
2. **Every rebuild was total data loss.** `homelab nuke` wipes
   `/var/lib/rancher/k3s/storage`. The repo treats `nuke && install` as a routine
   drill, so the default StorageClass was one whose contents the routine drill
   destroys.
3. **The unevenness was load-bearing.** `k3s-worker-2` has ~57Gi against ~115Gi
   on the other two, so PVC sizing was constrained by the smallest node rather
   than by the actual requirement.

## Decision

`nfs` is the default StorageClass, and `local-path` is removed entirely:
k3s is started with `--disable=local-storage`.

Every infra PVC moves: cloudnative-pg (10Gi), Prometheus (10Gi), Grafana (2Gi),
OpenBao (2Gi). Applications that name no `storageClassName` — which is all of
them except `rh-dashboard` — inherit `nfs` automatically. `prep-tracker`'s
CloudNativePG cluster is in this category and moves without an overlay change.

### Why remove `local-path` rather than annotate it non-default

k3s owns the `local-path` StorageClass through a wrangler objectset
(`objectset.rio.cattle.io/owner-gvk: k3s.cattle.io/v1, Kind=Addon`) and
reconciles it on every server start. An ArgoCD-managed `is-default-class: false`
annotation would be reverted on each k3s restart and re-applied by selfHeal —
two controllers fighting over one object, which is the failure mode this repo
avoids everywhere else (see the Traefik and servicelb disables). Disabling the
addon at its source has no such conflict.

The cost is that there is no local-disk escape hatch. Restoring one means
re-enabling the addon and a k3s server reinstall.

## What this trades away

This is not a free improvement, and the comments it replaced were not wrong.

- **Prometheus documents NFS as unsupported** for its local TSDB. The stated
  failure mode is silent, unrecoverable corruption rather than a clean error.
  Mitigation: retention stays at 7d/8GB, and the recovery plan for metrics is to
  start from empty.
- **PostgreSQL assumes a completed `fsync` is durable.** An NFS server that
  buffers writes can violate that across a power cut, undetectably, surfacing
  later as a corrupt page.
- **Replication no longer covers storage loss.** Both CloudNativePG instances now
  write to the same NAS. Pod anti-affinity still covers node loss — the common
  failure here — but `portal` going away takes primary and replica together.
- **`portal` becomes a cluster-wide single point of failure.** It is one 4GB Pi.
  Under `hard` mounts an outage blocks every stateful pod in uninterruptible
  sleep rather than corrupting it, and they recover when it returns. That is the
  right failure mode, but the blast radius went from one dashboard to all state.
  The server node has previously logged thousands of
  `nfs: server 192.168.11.3 not responding` timeouts, so this is a live risk and
  not a theoretical one.

## What depends on `portal`, and what does not

An NFS-backed PVC **is** a startup dependency — the kubelet must complete the
mount before the container starts, and an unreachable NAS leaves the pod in
`ContainerCreating` reporting only `exit status 32`. There is no way to make a
PVC non-blocking; the only non-blocking storage is `emptyDir`, which is never
NFS. So it is worth being precise about which pods gained that dependency:

| Layer | Blocks on `portal`? | Why |
|---|---|---|
| k3s, Calico, MetalLB, Traefik, cert-manager, tailscale-operator, ArgoCD, CNPG operator | **no** | no PVCs at all |
| `nfs-provisioner` itself | no (bootstrap) | its own PVC binds a **static** PV pointing straight at `192.168.11.3`, so it does not depend on the class it provides |
| `postgres` ×2, `prep-tracker-db` ×3, Prometheus, Grafana, OpenBao | **yes** | NFS-backed PVC |
| everything's `emptyDir`, image layers, container logs | no | node-local under `/var/lib/rancher/k3s` |

The layering is the point: a cold boot with the NAS down still produces a working
cluster with a syncing ArgoCD. Only the stateful leaves stall, and they recover
on their own when `portal` returns. Nothing in the control plane was moved onto
the NAS, and nothing should be.

## Anti-affinity is now load-bearing

With storage independence gone, node separation is the only remaining protection
against correlated failure of a CloudNativePG pair. That made a latent default
matter: **CNPG's `enablePodAntiAffinity: true` renders a `preferred` (soft)
rule**, not a required one. Verified against the live pod spec, which produced
`preferredDuringSchedulingIgnoredDuringExecution`.

Soft was acceptable when a co-located pair still meant two copies on two disks.
It is not acceptable now, so the shared cluster sets `podAntiAffinityType:
required` explicitly. 2 instances across 3 nodes is comfortably satisfiable.

`prep-tracker-db` still renders `preferred`, because its `Cluster` comes from the
app repo rather than this one. Fixing it means a change in
`thetaskmaster42/prep-tracker` and a ref bump in the overlay — outstanding.

## Consequences

- `mountOptions` stay `hard` deliberately. `soft` would return `EIO` under load,
  and every workload here treats a short read as corruption rather than as a
  retryable error. Blocking is the safe failure; losing data quietly is not.
- `reclaimPolicy: Retain` means a `nuke` leaves the data behind. Reclaiming space
  is now a manual step against the NAS.
- `volumeBindingMode: Immediate` replaces `WaitForFirstConsumer`. Correct here —
  the volume is not node-local, so there is nothing to wait for.
- **Backups become the load-bearing control and do not exist yet.** With
  `local-path` the second CloudNativePG instance was a real independent copy.
  It is not any more. Barman backups to a target that is not `portal` are now
  the highest-value follow-up in the repo.

## Migration

`spec.storageClassName` is immutable on a bound PVC, and StatefulSet
`volumeClaimTemplates` are immutable too, so this cannot be applied to a running
cluster by editing values alone — the sync fails on the immutable field. The
`--disable=local-storage` flag additionally only takes effect on a k3s server
reinstall, since it is baked into `INSTALL_K3S_EXEC`.

Both point at the same answer: `homelab nuke && homelab install`. That is the
supported path and the one this change was validated against.
