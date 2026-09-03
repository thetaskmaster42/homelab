# 0017 — Journiv, and what a third-party app costs when it ships a compose file

**Status:** accepted, 2026-09-03

## Context

[Journiv](https://github.com/journiv/journiv-app) is a self-hosted private
journal: entries, mood tracking, media uploads, search, import/export. It is
beta software, under active development, and its own README asks you to keep
backups.

It is the third app here whose source is not ours (after draw.io and openGym),
and the first that is genuinely multi-process. Upstream ships
`kubernetes/manifest.yaml`, and none of it is usable as-is:

| Upstream manifest | Why it cannot be used |
|---|---|
| `SECRET_KEY: "mylongsecretkeyyoushouldchange"` | a plaintext JWT signing key, in a public repo |
| `storageClassName: local-path` | node-pinned, and erased by `homelab nuke` |
| `image: swalabtech/journiv-app:latest` | floating; `tests/test_apps.py` rejects it |
| SQLite in the container | one file, and three pods want to open it |
| no celery, no valkey | import and export never complete |

So the real reference is `docker-compose.yml`, translated. That translation is
the substance of this ADR.

## Decision

Five workloads in the `journiv` namespace, all from local manifests in
`apps/journiv/` — the shape draw.io uses, for the same reason: third-party
source, so there is no `k8s/` base of ours to pull.

| Workload | Notes |
|---|---|
| `journiv` | FastAPI + gunicorn, serves the SPA and the API. One replica. |
| `journiv-db` | CloudNativePG Cluster, 3 instances, `nfs` |
| `valkey` | broker, rate-limit store, RedBeat lock. `emptyDir` |
| `journiv-celery-worker` | import/export |
| `journiv-celery-beat` | scheduled export cleanup |

### PostgreSQL, not SQLite

Upstream recommends it, and here it is closer to forced: the app pod and both
celery pods mount one `/data` volume, so a SQLite file on NFS would be three
writers on a network filesystem. CloudNativePG also generates the credential and
publishes it as `journiv-db-app`, so the database password is one nobody chooses
and nobody writes down — the shape [ADR 0014](0014-sops-as-the-only-secret-manager.md)
names as preferred.

`DATABASE_URL` is wired from that Secret's `uri` key, and `POSTGRES_PASSWORD` is
deliberately left unset: Journiv fails startup validation if both are present.

### One replica, because the entrypoint migrates

`docker-entrypoint.sh` runs `alembic upgrade head` on every start of the `app`
role. There is no flag to separate the two, so a second replica is a second
concurrent migration and a rolling update runs the new schema against the old
pod. `replicas: 1` and `strategy: Recreate` are the constraint, not a default.

prep-tracker solves this properly with a separate migrate Job; Journiv gives us
nothing to hook, so the constraint moves into the Deployment.

### The celery worker bypasses the entrypoint

This is the only place the deployment departs from upstream's compose file, and
it is because of an upstream bug worth writing down.

`docker-entrypoint.sh` branches on `SERVICE_ROLE` and `exec`s celery **before**
it ever inspects its arguments. Compose passes the worker's tuning flags as
`command:`, which Docker appends as arguments — so `--concurrency`,
`--max-memory-per-child` and `--max-tasks-per-child` are all silently discarded,
in compose as much as here. Celery then defaults to one prefork child per CPU:
four on a Pi 5, each holding a full copy of the application, to run one export at
a time.

Calling celery directly is the only way to actually set the flag. The pod's
`command` re-does the one other thing the entrypoint did for this role
(`mkdir -p /data/media /data/logs`) and nothing else — in particular it does not
migrate, because the app pod owns the schema.

`celery-beat` keeps the entrypoint: its branch already passes the RedBeat
scheduler and the pidfile, and there is nothing to override.

### `/data` is ReadWriteMany

An export is written by a worker and downloaded through the app, so they must
see one filesystem. Only the `nfs` class can do that here — `local-path` is
node-pinned and ReadWriteOnce. It is also `reclaimPolicy: Retain`, so the
journal's media survives `homelab nuke`.

It does not survive `portal` dying, and nothing in this cluster is backed up.
For an app whose own README asks for backups, that gap is now the most pointed
it has been.

## Tailnet only, for now

Every other app here got a LAN Ingress in [ADR 0016](0016-lan-ingress.md).
Journiv does not, yet, and the reason is signup.

Journiv has real authentication, but no way to create the first account except
the public signup form — `journiv-admin` can change a password and import data,
it cannot create a user. So `DISABLE_SIGNUP` has to start `false`, and until
somebody registers, whoever reaches the app first becomes the account.

On a one-member tailnet the network closes that window. On the LAN it would be
open to every device on the subnet, which is the same reasoning that keeps
openGym off the LAN plane ([ADR 0015](0015-opengym.md)).

The sequence, in order:

1. register at `https://journiv.mongoose-galaxy.ts.net`
2. set `DISABLE_SIGNUP=true` in `apps/journiv/kustomization.yaml` and push
3. then add `apps/journiv/lan-ingress.yaml`

Step 3 costs more than it does for the other apps, and the first deploy proved
it. `DOMAIN_NAME` is not merely the base URL Journiv puts in OIDC redirect and
post-logout links — in production with CORS disabled it is also the entire
allowlist for FastAPI's `TrustedHostMiddleware`, alongside `localhost` and
`127.0.0.1` (`app/main.py:216`). Measured against the running pod:

```
Host: <pod IP>              400
Host: localhost             200
Host: journiv.rps-home.com  400
```

So a LAN Ingress does not merely generate links pointing at the tailnet name —
**every LAN request is rejected before it reaches a route**. Making it work means
setting `ENABLE_CORS=true` with `CORS_ORIGINS` naming both origins, which
switches the allowlist to the CORS branch. That is a real change to the app's
security posture, not an extra Ingress file, and it should be its own decision.

The same middleware is why both probes have to send `Host: localhost`. Without
it the kubelet sends the pod IP, gets a 400 from an application that started
perfectly, and liveness crashloops it with nothing in the log but
"Request completed with client error".

## What this gives up

- **Rolling updates.** `Recreate` means a few seconds of downtime on every
  image bump, spent running migrations. Correct, and cheap for one user.
- **Celery concurrency.** One task at a time. An import of a large archive
  blocks an export behind it.
- **Valkey durability.** In-flight tasks die with the pod. Costs a re-run.
- **A second origin.** No LAN name until signup is closed.

## Consequences

- `SECRET_KEY` joins the bootstrap bundle as `journiv-app-secret`. It is the
  fourth entry, and the first belonging to an application rather than to the
  platform — the bundle is now doing slightly more than ADR 0014 scoped it for,
  which is worth watching rather than fixing today.
- Rotating it invalidates every session and every password-reset token in flight.
  It does not invalidate accounts.
- Journiv is beta. The pin is `0.1.0-beta.24`; upstream has no stable release,
  and bumping it means reading their migration notes rather than trusting semver.
