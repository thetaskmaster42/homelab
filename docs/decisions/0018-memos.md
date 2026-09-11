# 0018 — Memos alongside journiv, and why "needs Postgres" is the cheap option

**Status:** accepted, 2026-09-10
**Related:** [ADR 0017](0017-journiv.md), which this deliberately duplicates.

## Context

We wanted a note-taking app. journiv ([ADR 0017](0017-journiv.md)) is already
here and is a *journal* — dated entries, mood, media. The overlap with a notes
app is real, and rather than argue it in the abstract the decision is to run
both for a while and keep one. Whichever loses is `git rm -r apps/<name>`; the
ApplicationSet finalizer takes the workloads with it.

## The survey, and the thing that inverted it

arm64 was not the discriminator this time — Memos, SilverBullet, Trilium,
Flatnotes, Docmost, HedgeDoc, Wiki.js and Outline all publish `linux/arm64`
today. What separated them was **dependency footprint measured against this
cluster**, and the naive ranking turns out to be backwards here:

- **Postgres is free.** CloudNativePG is already running. A database is one CR,
  and the operator generates the credential — so it needs no SOPS entry, which
  is the shape [ADR 0014](0014-sops-as-the-only-secret-manager.md) prefers.
- **SQLite is expensive.** SQLite's own guidance is to avoid network
  filesystems, and `nfs` is where application data lives here. A SQLite app is
  pushed onto `local-path` (node-pinned, erased by `homelab nuke`) or a `/srv`
  hostPath — the opengym pattern, which is one SD card and nothing backed up.

| | Needs besides itself | Storage | arm64 image |
|---|---|---|---|
| **Memos** | nothing | Postgres (optional) | 23 MB |
| SilverBullet | nothing | plain markdown folder | 41 MB |
| Trilium | nothing | **SQLite only** | 167 MB |
| Docmost | Postgres + **Redis** | Postgres + files | — |
| Outline | Postgres + Redis + **S3** + **OIDC** | — | — |

Outline is ruled out on that row alone: it needs MinIO, which is still on the
deferred list, *and* an external identity provider. Trilium is the best product
of the three light ones and still loses here, because SQLite-only forces the
hostPath.

## Decision

Memos, on CloudNativePG, exposed on **both** the tailnet and the LAN.

`MEMOS_DRIVER=postgres` with `MEMOS_DSN` from the operator's `memos-db-app`
secret. The driver is `lib/pq`, which defaults to `sslmode=require` when the DSN
is silent; CNPG serves TLS from its own CA and `require` does not verify the
chain, so the bare `uri` connects with no override.

### It gets a LAN name, and journiv does not

This looks inconsistent and is not. journiv's `DOMAIN_NAME` doubles as its
`TrustedHostMiddleware` allowlist, so a second hostname is rejected with 400
before reaching a route — a fact that cost a crashloop to discover.

Memos has no such allowlist. `server/cors.go:47` grants credentialed CORS to any
origin whose host equals **the host of the incoming request**, falling back to
the configured instance URL only if they differ:

```go
if strings.EqualFold(originURL.Host, requestHost) { return true }
```

So `MEMOS_INSTANCE_URL` is left deliberately **unset**. Empty is valid
(`normalizeInstanceURL` returns early), and leaving it empty means there is no
canonical origin to contradict — both hostnames are same-origin and both work.

### Signup is closed from the first boot

journiv's unsatisfying part was that `DISABLE_SIGNUP` had to *start* false: the
first account can only come from the signup form, so closing it early locks
everybody out permanently. That left a window.

Memos has no such window. The first user is created through a separate atomic
path (`CreateUserIfNoUsers`) that assigns `RoleAdmin`, and the
`DisallowUserRegistration` check is skipped for admins — so registration can be
disallowed from boot and the first account still works.

Better still, that setting is declarative. Memos scans `/etc/secrets` at startup
for `memos-instance-setting-*.json` and anything configured there becomes
**read-only in the admin UI** — the API refuses to update a deployment-configured
setting. So it is a ConfigMap in git, not a checkbox somebody has to remember:

```json
{ "key": "GENERAL", "generalSetting": { "disallowUserRegistration": true } }
```

The filename is load-bearing — it must match
`^memos-instance-setting-[a-z0-9-]+\.json$` or the file is ignored with only a
log line to say so.

### No bootstrap secret at all

Memos generates its own session secret into `InstanceBasicSetting` and keeps it
in the database. Nobody chooses it and nobody writes it down. Combined with the
operator-generated database password, Memos adds **nothing** to
`bootstrap-secrets.enc.yaml` — the first app here to need no entry.

## What this gives up

- **Rolling updates.** One replica and `Recreate`, because Memos migrates on
  start with no separate step to hook. Seconds of downtime per bump.
- **Attachments live in the database** by default, so image uploads grow the
  Postgres volume rather than the 1Gi data PVC. Sized at 5Gi for that reason.
- **A decision deferred.** Running two overlapping apps is the point, but it is
  still two things to keep patched until one is retired.

## The deploy found one thing the review did not

Memos came up, connected to Postgres, migrated its schema and loaded the
deployment configuration — and was unreachable. It logged
`Server running on port 0` and listened on a random ephemeral port.

The cause is not in Memos. Kubernetes injects legacy Docker-link variables for
every Service in the namespace, and our Service is named `memos`, so the kubelet
set `MEMOS_PORT=tcp://10.43.x.x:80` — overwriting the `MEMOS_PORT=5230` the image
sets. Memos reads config through viper's `AutomaticEnv` with prefix `MEMOS`, so
`GetInt("port")` parsed that URL as `0`.

The app choosing an env prefix equal to its own Service name is what exposes it,
and that is not unusual. Fixed with `enableServiceLinks: false` plus an explicit
`MEMOS_PORT`, either of which is sufficient; both are there because the switch
removes the class of bug and the env var survives someone re-enabling links.
Recorded in CLAUDE.md, because the next app with a matching prefix will hit it.

## Consequences

- The data PVC is required even with Postgres: Memos refuses to start without a
  writable data directory. Almost nothing lands in it.
- The container never runs as root. The image's entrypoint chowns and drops
  privileges only when started as uid 0; starting as 10001 skips that branch,
  and `fsGroup` does the ownership work instead.
- Retiring the loser is one `git rm -r`. Retiring **journiv** additionally means
  its `SECRET_KEY` should come out of the bootstrap bundle.
