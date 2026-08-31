# 0008 — Local disk for Prometheus and OpenBao

**Status:** accepted, 2026-08-26 — **partially overtaken.** OpenBao was removed
in [ADR 0014](0014-sops-as-the-only-secret-manager.md), so half of what this
ADR placed on local disk no longer exists. The reasoning for the monitoring
stack stands unchanged, and the reconstructibility argument it introduces is
still the rule for deciding where a volume belongs.
**Amends:** [ADR 0006](0006-nfs-default-storage.md), which made `nfs` the default
*and only* StorageClass. The "only" half is reversed here; the default half stands.

## Context

ADR 0006 moved every PVC onto the `portal` NAS and disabled k3s's `local-storage`
addon. It named the cost honestly — `portal` became a hard dependency of every
stateful workload — and accepted it in exchange for volumes that survive node
loss and `homelab nuke`.

Working through what a NAS outage actually does made that trade look worse than
it did on paper. The mount options are `hard`, so a process blocking on NFS I/O
enters **uninterruptible sleep**. It does not receive an error. It cannot be
killed, not by SIGKILL and not by the kubelet.

Three consequences followed that ADR 0006 did not weigh:

1. **Observability fails exactly when needed.** Prometheus' TSDB write path
   blocks, scrapes buffer in memory until the pod is OOMKilled, and the unclean
   restart replays a WAL that upstream explicitly warns NFS may have corrupted.
   Prometheus *is* the alerting, so a NAS failure silences the system that would
   have reported it.
2. **OpenBao blocks recovery, not just service.** A blocked OpenBao means the
   injector cannot render secrets, so no new pod needing an injected secret can
   start. The outage stops being something you can deploy your way out of.
3. **Pods hang rather than fail.** Uninterruptible sleep means stuck
   `Terminating` pods, which makes `kubectl drain` hang and blocks clean node
   maintenance.

Meanwhile the thing ADR 0006 was protecting — data that cannot be reconstructed —
does not describe either workload. Metrics are the most disposable data here.
OpenBao's volume is a cache of unsealed state, rebuildable by re-initialising and
re-seeding from the SOPS bundle.

## Decision

Re-enable k3s `local-storage`. Move the **whole monitoring stack**
(Prometheus *and* Grafana) and **OpenBao** to `local-path`. Everything else — the
shared CloudNativePG cluster, `prep-tracker-db`, `rh-dashboard` — stays on `nfs`.

The split is by **reconstructibility**, not by importance:

| Data | Class | Why |
|---|---|---|
| Metrics (Prometheus) | `local-path` | disposable; must survive a NAS outage |
| Dashboards/prefs (Grafana) | `local-path` | provisioned from ConfigMaps; must render during an outage |
| Unseal state (OpenBao) | `local-path` | reconstructible; gates all recovery |
| Application + database data | `nfs` | not reconstructible; `nuke` must not erase it |

Grafana is included on a whole-stack argument rather than a per-volume one. Its
SQLite file would have been safe on NFS — `nfsvers=4.1` provides real
in-protocol locking — but a monitoring stack half on the NAS is a monitoring
stack that goes half-dark during a NAS outage: Prometheus still scraping,
Grafana unable to render. The component that reports on a failure must not
depend on it.

## The two-defaults problem, and why it is safe

k3s marks `local-path` as a default StorageClass and **we cannot stop it.** The
addon is a wrangler objectset (`owner-gvk k3s.cattle.io/v1, Kind=Addon`)
re-applied on every server start, so an ArgoCD-managed annotation flip would be
two controllers fighting over one object — the failure mode this repo avoids
everywhere else. `nfs` is also default, for applications.

So **two default StorageClasses exist**. Kubernetes breaks the tie by creation
timestamp, which is to say arbitrarily, and differently after every rebuild.

That is tolerable for exactly one reason: **nothing relies on the default.**
Every PVC in this repo names its class explicitly, and
`tests/test_apps.py::test_every_volume_claim_names_its_storage_class` fails the
build if one does not — covering standalone PVCs, StatefulSet
`volumeClaimTemplates`, and CloudNativePG `.spec.storage` / `.spec.walStorage`.

`prep-tracker-db` was the live example: its `Cluster` comes from the app repo and
declared no class, so it inherited the default. It is now pinned to `nfs` by a
kustomize patch in the overlay. The failure that prevents is silent — the
database would have worked perfectly on node-local disk and then been erased by
the next `homelab nuke`, with no error anywhere.

### Alternatives considered

- **A community Helm chart for local-path-provisioner**, keeping `local-storage`
  disabled and one clean default. Rejected: Rancher publishes no official Helm
  repo (`charts.rancher.io` does not carry it), so this would take a
  supply-chain dependency on an individual's chart repo for a component holding
  cluster-scoped storage RBAC.
- **Static `hostPath` PVs with `nodeAffinity`** and a no-provisioner class.
  Workable and fully declarative, but a hand-written PV per workload with fixed
  sizes, for no benefit over the provisioner k3s already ships.
- **Deploying the provisioner under `apps/`**, which needs no chart. Rejected:
  the `apps` AppProject is not scoped for cluster-scoped resources.

## Consequences

- **Prometheus and OpenBao are node-pinned again.** If their node dies, the
  volume does not move and the workload does not reschedule. Accepted: both are
  reconstructible.
- **`homelab nuke` destroys all three volumes.** Expect to re-initialise and
  re-seed OpenBao after every rebuild — it was already part of the drill — to
  start metrics from empty, and to lose saved Grafana preferences. Dashboards
  themselves come back from ConfigMaps.
- **The monitoring stack is now entirely independent of `portal`.** During a NAS
  outage Prometheus keeps scraping, Grafana keeps rendering, and OpenBao keeps
  serving secrets — so you can both see the failure and deploy your way out of
  it. That is the property this ADR exists to buy.
- **The `nuke` PVC warning becomes meaningful again**, listing real doomed
  volumes rather than nothing.
- **Backups are still the top gap.** Nothing here changes that both CloudNativePG
  instances write to the same NAS with no backup target off it.
