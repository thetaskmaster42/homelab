# PostgreSQL

**One CloudNativePG `Cluster` per application, with one instance each.** There is
no shared database. The operator is infrastructure; every database is part of the
application that owns it.

See [ADR 0020](decisions/0020-one-postgres-per-app.md) for why the shared cluster
was removed after running for weeks with zero consumers, and why three instances
turned out to be theatre.

## How it gets installed

`infra/services/cloudnative-pg/` installs the **operator only** —
`extraManifests: "false"`, no `manifests/` directory. Each application then
declares its own `Cluster` beside its own manifests:

```
apps/journiv/postgres-cluster.yaml         journiv-db   → ns journiv
apps/memos/postgres-cluster.yaml           memos-db     → ns memos
apps/daily-trade-tracker/postgres-cluster.yaml
                                           trade-tracker-db → ns daily-trade-tracker
apps/prep-tracker/kustomization.yaml       prep-tracker-db (remote base, patched)
```

**Nothing creates database pods directly.** A `Cluster` is declared; the operator
watches for it and builds everything else. That indirection is why
`kubectl get pods -n memos` shows objects with no matching manifest in this repo.

An operator rather than a plain postgres chart because failover, rolling minor
upgrades and (eventually) barman archiving are the interesting parts of running a
database, and CloudNativePG models them as Kubernetes objects rather than hiding
them in a StatefulSet you then reason about by hand. None of that value depended
on there being one big cluster.

## Why per-app, briefly

Two arguments carry it; the rest is noise.

1. **Extensions and major versions are cluster-wide.** Immich needs VectorChord in
   `shared_preload_libraries`. In a shared cluster every application inherits one
   application's extensions and engine version.
2. **Lifecycle.** `git rm -r apps/memos` takes its database with it. A shared
   cluster leaves an orphaned database and role — the breakage the
   `resources-finalizer` exists to prevent.

It also removes the old **namespace problem** for free. Secrets are
namespace-scoped, so an app in `interview` could never read a Secret in
`databases`; that used to need `Database`/`DatabaseRole` CRDs, a
secret-replicating controller, or a secrets engine. With a per-app cluster the
operator writes `<cluster>-db-app` straight into the namespace that needs it.

## What the operator builds

From `spec.instances: 1`:

| Object | Detail |
|---|---|
| `<cluster>-1` | the primary — and the only instance |
| `<cluster>-rw`, `-ro`, `-r` | three Services (below) |
| `<cluster>-1` PVC | on `nfs`, always named explicitly |
| `<cluster>-app` | application credentials, `kubernetes.io/basic-auth` |
| `<cluster>-ca`, `-server`, `-replication` | TLS for client and replication traffic |

## One instance, and what that costs

Replicas protected against the failure this lab does not have. Every volume is on
the same NAS, so `portal` going away took primary and replica together; and a
k3s-server blackhole removes the API server, at which point CNPG's instance
manager exits on every node at once. ~1,900 accumulated postgres restarts were
correlated failure being counted three times per cluster, not resilience.

**Availability is not zero at one instance.** The PVC is `ReadWriteOnce` but the
volume is NFS, which does not enforce that the way a block device does, so the pod
reschedules onto another node and reattaches. Node loss costs minutes (bounded by
the node-not-ready eviction timeout) instead of seconds, and no data.

What is genuinely given up: fast failover, and read replicas nothing was using.

## Why the nfs class, despite the case against it

The argument for `local-path` was sound while there were replicas:

> CloudNativePG gets durability from **streaming replication between nodes**, not
> from shared storage. PostgreSQL also depends on strict `fsync` semantics, and
> that is exactly what NFS is worst at.

[ADR 0006](decisions/0006-nfs-default-storage.md) overrode it, and with one
instance the first half of that argument no longer applies at all. What remains
decisive is that **`homelab nuke` erases every `local-path` volume**, and
application data is the one thing here that cannot be reconstructed.

The `fsync` caveat still stands and is now unmitigated: Postgres treats a
completed `fsync` as durable, an NFS server that acknowledges early breaks that
across a power cut, and Postgres cannot detect it — the damage surfaces later as
a corrupt page. `hard` mounts cover a NAS *outage* cleanly (block, then resume);
they do nothing for a power cut on `portal` mid-write.

**So backups are the only real protection, and they still do not exist.** See the
last section.

## The three Services

They differ only in their selector, and with one instance all three resolve to
the same pod — which is exactly why you should still use the right one. When an
app later grows a replica, code written against `-rw` keeps working.

| Service | Selector | Use for |
|---|---|---|
| `<cluster>-rw` | `instanceRole: primary` | **writes** — and reads that must see them |
| `<cluster>-ro` | `instanceRole: replica` | read-only queries (nothing today) |
| `<cluster>-r` | `podRole: instance` | any instance, primary included |

`instanceRole` is a **label the operator moves during failover**, so `-rw` follows
the primary with no DNS change, no config change and no application restart.

The corollary holds regardless of instance count: **never connect to a pod
directly.** `<cluster>-1` is only the primary until it isn't.

## How applications connect

`<cluster>-app` carries everything, already assembled:

| Key | Contents |
|---|---|
| `username`, `user` | the `owner` from `bootstrap.initdb` |
| `password` | generated by the operator, 64 chars, URL-safe |
| `dbname` | the `database` from `bootstrap.initdb` |
| `host`, `port` | `<cluster>-rw`, `5432` |
| `uri`, `jdbc-uri` | connection strings, short host |
| `fqdn-uri`, `fqdn-jdbc-uri` | connection strings, `*.svc.cluster.local` |
| `pgpass` | ready for `PGPASSFILE` |

> **The `*-uri` keys embed the password.** Never echo, log, or paste them. Mount
> them; do not print them.

### The `uri` key is not always usable — check the driver

`uri` uses the scheme `postgresql://`. **SQLAlchemy 2.0 resolves that to
psycopg2**, so an application that ships only `psycopg[binary]` (psycopg 3) fails
at import with `ModuleNotFoundError: No module named 'psycopg2'` — which reads as
a broken image rather than a wrong connection string. daily-trade-tracker hit
exactly this.

The fix is to compose the URL with an explicit dialect from the component keys,
relying on Kubernetes expanding `$(VAR)` from earlier env entries:

```yaml
- name: DATABASE_URL
  value: "postgresql+psycopg://$(DB_USER):$(DB_PASSWORD)@$(DB_HOST):$(DB_PORT)/$(DB_NAME)"
```

Safe because the operator's generated password is 64 characters from a URL-safe
alphabet — checked across every CNPG cluster here. A password containing `@` or
`/` would silently corrupt the URL, so that is the assumption to re-check if
CNPG ever changes its generator.

## Operating it

```sh
# health, and which instance is primary
kubectl -n <ns> get cluster <cluster>

# a psql shell
kubectl -n <ns> exec -it <cluster>-1 -- psql -U postgres

# rotate the application password: delete the Secret and the operator
# regenerates it AND applies it to the role. Verify the old one then fails.
kubectl -n <ns> delete secret <cluster>-app
```

Adding a replica is a one-line change to `spec.instances`. Do it per application,
with a comment saying why that application needs it — not by copy-paste.

## Not yet done

**No backups, on any cluster.** `spec.backup` is unset everywhere, so there is no
WAL archiving and no point-in-time recovery. Replication was never a substitute:
copies of a table someone dropped are still copies of a dropped table, and now
there is only one copy.

CloudNativePG archives to S3-compatible storage. MinIO on the NAS is the obvious
target. **This is the highest-priority gap in the repo** — and reducing replicas
(ADR 0020) deliberately made it more urgent rather than less.
