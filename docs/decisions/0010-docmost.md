# 0010 — Docmost on the HelmForge chart, single replica for now

**Status:** accepted, 2026-08-27

## Context

A collaborative wiki was wanted: Docmost, the Confluence/Notion shape. It needs
PostgreSQL, Redis, and somewhere to put uploaded files.

There is no official Helm chart. Three community ones exist:

| Chart | App version | Last updated |
|---|---|---|
| **`helmforge/docmost` 1.2.10** | **0.95.0** (current) | **days ago** |
| `th-charts/docmost` 0.5.3 | 0.24.1 | behind by years of releases |
| `acidsugarx/docmost` 0.1.3 | 0.23.2 | Nov 2025 |

HelmForge is the only live one. It is GPG+cosign signed, declares `stable`
maturity, and makes both PostgreSQL and Redis conditional subcharts — which is
what allows the database to be swapped for CloudNativePG.

`docmost/docmost:0.95.0` carries arm64. The `-cf.beta` tags do not: they are
Cloudflare variants published amd64-only, and must be avoided. The Redis
subchart uses `docker.io/library/redis` — the official image, not Bitnami, which
sidesteps Bitnami's registry changes entirely.

## Decision

Deploy the HelmForge chart with **one replica**, uploads on `nfs`, Redis on
`local-path`, and PostgreSQL from CloudNativePG.

### Single replica, and why that is temporary

The chart hard-fails when `replicaCount > 1` unless `storage.mode` is `s3`:

```gotemplate
{{- if and (ne (int .Values.replicaCount) 1) (ne .Values.storage.mode "s3") -}}
{{- fail "docmost: replicaCount greater than 1 requires storage.mode=s3
          because local storage is single-writer" -}}
```

The guard is unconditional — it never inspects `accessMode`, so it cannot know
this cluster's `nfs` class is RWX and therefore genuinely multi-writer. Docmost
itself supports horizontal scaling, coordinating instances through Redis pub/sub
(the Hocuspocus collaboration layer). So the constraint is the chart's, not the
application's.

A Helm `fail` aborts at template time, so it cannot be patched around by
kustomize or ArgoCD. The options were:

- **Hand-write the manifests** in `apps/`, dropping the chart. Gets two replicas
  on RWX NFS today.
- **Wait for S3**, then move to `storage.mode: s3` and raise the replica count.

S3 is on the roadmap, and that settles it. Verified from Docmost's source: the
`StorageService` hands a driver-agnostic `filePath` to whichever driver is
configured — `LocalDriver` roots it at `LOCAL_STORAGE_PATH`, `S3Driver` at the
bucket. The relative keys are identical and the database stores that same path
either way, so **migrating is a file copy into the bucket with no database
rewrite**. (Docmost does not migrate existing files itself; only new uploads
follow a driver change.)

That makes the move roughly ten lines of values plus a one-time copy, on the
same Application with no resource churn. Hand-writing the manifests would
instead be discarded work, and would carry a data-reattachment hazard: replacing
the Application prunes its PVC, and while `Retain` keeps the bytes on `portal`,
the new claim provisions a fresh empty directory.

The cost of waiting is no HA for the app tier. Postgres is already replicated.

### ReadWriteMany at one replica

Not redundant. The chart sets no `updateStrategy`, so the Deployment defaults to
`RollingUpdate` with `maxSurge: 1` — a second pod starts *before* the old one
exits on every upgrade. Under `ReadWriteOnce` that second pod would block on the
volume and the rollout would stall. RWX costs nothing on this class.

### Storage split

Follows [ADR 0008](0008-local-disk-for-observability-and-secrets.md), by
reconstructibility:

| Data | Class | Why |
|---|---|---|
| Uploaded files | `nfs` | no other copy exists |
| PostgreSQL | `nfs` | no other copy exists |
| Redis | `local-path` | coordination and queues; reconstructible |

### CloudNativePG, and the extensions

A dedicated `docmost-db` cluster rather than a database in the shared one, so
its extensions do not land on every other application's database.

**Docmost requires the `unaccent` and `pg_trgm` extensions and fails on first
start without them.** The chart's bundled PostgreSQL installs them in an initdb
script; CloudNativePG knows nothing about that. They are declared in
`manifests/cluster.yaml` via `postInitApplicationSQL`, which runs as superuser
inside the freshly created database — the privilege `CREATE EXTENSION` needs and
the `docmost` role lacks. This is the easiest thing to miss when swapping the
bundled database for CNPG.

Anti-affinity is `required`, not the CNPG default of `preferred`, for the reason
in ADR 0008: with both volumes on the same NAS, node separation is the only
protection left and a soft rule stops applying under pressure.

## The determinism trap

The chart generates `app-secret` and the Redis password with `randAlphaNum` when
values leave them empty, reusing existing values through Helm `lookup`. That
works for `helm upgrade` against a live cluster. **It does not work under
ArgoCD**, which renders client-side where `lookup` returns nothing.

Left alone, both Secrets would regenerate on every refresh, the Deployment's
`checksum/secret` annotation would change with them, and the pod would restart
every few minutes — logging out every user each time, since `APP_SECRET` signs
the JWTs. This is the same failure `tests/test_render_determinism.py` exists to
catch, and the same one already documented for Grafana's admin password.

Redis was straightforward: the subchart offers `auth.existingSecret`.

`appSecret` has no such option — the chart hardcodes its Secret name. The fix is
two-part, and neither half works alone:

1. Set `docmost.appSecret` to a **harmless placeholder**. Any value stops the
   randomisation and makes the render deterministic.
2. Inject the real value through `docmost.extraEnv` as `APP_SECRET` from a
   Secret in the SOPS bootstrap bundle. It renders *after* the chart's own env
   entry, and Kubernetes takes the last of a duplicated name.

The placeholder cannot be the real secret because this repo is public. Verified:
the configuration now renders byte-identically across runs.

## Consequences

- **One replica.** An upgrade or a node failure is a brief outage. Removed when
  S3 lands.
- **`homelab bootstrap` must run before this syncs.** It applies
  `docmost-app-secret` and `docmost-redis-auth` and creates the namespace;
  without them the pod cannot start. Bootstrap is idempotent.
- **WebSockets must survive the ingress.** Docmost's editor uses them; if
  `Upgrade`/`Connection` headers are not forwarded the page loads but is
  read-only. Verify after deploying.
- **Redis is node-pinned.** If its node dies, realtime collaboration degrades
  until it returns. Documents are in PostgreSQL and unaffected.
- **This is the strongest argument yet for backups.** A wiki is the first thing
  here whose loss would genuinely hurt, and both database instances write to one
  un-replicated 4GB Pi with no backup target off it.
