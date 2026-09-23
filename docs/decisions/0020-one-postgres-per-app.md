# 0020 — One Postgres per app, one instance each

**Status:** accepted, 2026-09-23
**Reverses:** the shared-cluster premise of `infra/services/cloudnative-pg/manifests/cluster.yaml`,
which promised *"Applications do not each run their own database. They get a
database and a role inside this one."* They never did.

## Context

Two questions got answered together: should applications share one PostgreSQL
cluster, and how many instances should a cluster have. Measurement settled both.

### The shared cluster had zero consumers

```
databases/postgres    2 instances    20Gi on nfs    883 restarts    consumers: NONE
```

It ran for weeks serving nothing. Every application built since — prep-tracker,
journiv, memos, daily-trade-tracker — declared its own `Cluster` instead. Nobody
decided to abandon the shared one; four independent choices simply went the other
way, which is the more honest signal.

This is [ADR 0014](0014-sops-as-the-only-secret-manager.md)'s OpenBao story
repeating: *"Not one application ever read a secret from it."* The same
conclusion applies for the same reason — **a component that is not used, but must
be kept alive, is not free.**

### The "shared is cheaper" argument does not survive measurement

Eleven Postgres pods consumed **518 MiB in total**, about 47 MiB each, against
48 GB of cluster RAM. The per-application tax is real but negligible at this
scale, and it was the main argument for consolidating.

### Two arguments for per-app are load-bearing

1. **Extensions and versions are cluster-wide settings.** Immich requires
   VectorChord in `shared_preload_libraries`. One shared cluster means every
   application inherits one application's extensions and major version. This is
   the argument that actually decides it, and it only becomes visible once a
   second app has opinions about the engine.
2. **Lifecycle matches this repo's central promise.** `git rm -r apps/memos`
   takes its database with it. A shared cluster leaves an orphaned database and
   role behind — the same class of breakage the `resources-finalizer` exists to
   prevent, and the reason "config-driven" is treated here as a guarantee rather
   than a slogan.

## Decision

**One `Cluster` per application, declared beside that application's manifests,
with `instances: 1`.** The CloudNativePG operator stays; only the shared cluster
goes.

## Why one instance and not three

Three instances was theatre, and this is the part worth being blunt about.

Every replica's volume is on the same NAS. `portal` going away takes primary and
replicas together, so replication never protected against the failure mode this
cluster actually has. Worse, when k3s-server blackholes — currently several times
a day — the API server disappears and CNPG's instance manager exits on every
node at once. That is what **~1,900 accumulated postgres restarts** recorded: not
resilience working, but correlated failure being counted three times per cluster.

What replicas buy: seconds-of-failover instead of minutes when a single node
dies. What they do not buy: protection from a bad migration, a wrong `DELETE`, or
a corrupt page — every one of which replicates to all three copies instantly.

And the state that made this indefensible:

```
databases/postgres        backup=NONE
interview/prep-tracker-db backup=NONE
journiv/journiv-db        backup=NONE
memos/memos-db            backup=NONE
```

**Twelve replicas and zero backups is backwards.** Replicas cover node loss;
backups cover everything else, including operator error. The freed capacity —
roughly eight pods and two-thirds of the WAL traffic crossing a single 1 GbE link
to one NAS — is meant for barman archiving to S3.

Availability does not drop to zero at one instance. The PVCs are
`ReadWriteOnce` but the volumes are NFS, which does not enforce that the way a
block device does, so the pod reschedules onto another node and reattaches. Node
loss costs minutes rather than seconds, and no data.

## What this gives up

- **Fast failover.** A lost node now means the pod is rescheduled (bounded by the
  node-not-ready eviction timeout, ~5 minutes) rather than a replica being
  promoted in seconds. Acceptable for personal applications; it would not be for
  anything with users.
- **Read replicas.** No `-ro` endpoint to send reporting queries at. Nothing used
  one.
- **Physical replication as a safety net while backups do not exist.** This is
  the real cost and it is sequenced wrong on purpose: reducing replicas before
  barman lands makes the window worse, not better. The decision was taken with
  that stated. **Barman → S3 is now the highest-priority item in the repo.**

## Consequences

- `infra/services/cloudnative-pg/` is operator-only: `extraManifests: "false"`
  and no `manifests/` directory. `tests/test_service_config.py` asserts those
  two agree.
- Deleting the `databases` Namespace prunes its PVCs, but the `nfs` class is
  `reclaimPolicy: Retain` with `archiveOnDelete: true`, so the data is renamed
  aside on the NAS rather than erased. Reclaiming that 20Gi is a manual cleanup
  on `portal`.
- Scaling each cluster 3 → 1 likewise archives the two surplus volumes per
  cluster rather than deleting them.
- Any future application needing genuine HA should say so in its own
  `Cluster` and explain why, rather than inheriting three instances by copy-paste.
