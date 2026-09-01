# 0015 — openGym on node-local storage

**Status:** accepted, 2026-09-01

## Context

openGym is a self-hosted gym and body-weight tracker: a React PWA, a small Node
API, and passkey login. Three moving parts — `api`, `web`, and a one-time job
that downloads ~140 MB of exercise images.

It publishes **no images**. The upstream compose file points at
`ghcr.io/duartesantos8/opengym-{api,web}`, both of which return 403, and upstream
has no CI workflows at all; self-hosters are expected to `docker compose up
--build`. A cluster that bans floating tags needs something to pin, so the fork
at `thetaskmaster42/openGym` carries a workflow that publishes both images for
`linux/arm64`, natively on `ubuntu-24.04-arm`. Both built in 0.9 minutes.

Two things found while building, recorded because they cost time:

- The compose file declares `dockerfile: web/Dockerfile`, but `web/` contains
  only `nginx.conf`. The real web image is the **root** `Dockerfile`. Upstream's
  compose would fail as written.
- Upstream's own Dockerfile warns that QEMU-emulated npm corrupts esbuild and
  rollup's platform binaries, surfacing as unrelated-looking module-resolution
  errors. Independent confirmation that native arm64 runners were the right call.

## The constraint that shapes everything: passkeys are bound to the hostname

The API takes `RP_ID`, `ORIGIN` and `RP_NAME`. In WebAuthn the **RP ID is
cryptographically baked into every credential at registration**. Change the
hostname and every passkey stops working — permanently, with no migration and no
recovery. Users simply re-register.

So `RP_ID` is `opengym.mongoose-galaxy.ts.net` and that name must never drift.
This makes openGym the first application here where the Tailscale `-1` suffix bug
would have been **destructive rather than cosmetic**: it would not have broken a
bookmark, it would have locked everyone out of an app that still looked healthy.
The device cleanup in `homelab nuke` is what prevents that.

It also forces a single origin. The web image's nginx proxies `/api` to the api
Service by name, so there is one Ingress on `web` and the api is ClusterIP-only.
**The api Service must be named `api`** — that hostname is baked into the image.

## Decision

Both volumes are **node directories under `/srv`**, with the pods pinned to that
node by `nodeSelector`.

| Volume | Path | Holds |
|---|---|---|
| data | `/srv/opengym/data` | users, passkeys, session secret, VAPID keys |
| media | `/srv/opengym/media` | ~140 MB of exercise images and GIFs |

### Why not a StorageClass

The requirement was that a rebuild must not re-download the media. Neither class
delivers that:

- **`local-path`** provisions under `/var/lib/rancher/k3s/storage`, which
  `k3s-uninstall.sh` deletes. Wiped by every `nuke`.
- **`nfs`** survives the nuke — `reclaimPolicy: Retain` — but a rebuilt cluster
  provisions a *fresh* subdirectory rather than reattaching the old one, so the
  volume comes back empty. This is exactly how `rh-dashboard`'s archive was
  orphaned after an earlier rebuild.

`/srv` is touched by neither `k3s-uninstall.sh` nor the CNI cleanup — verified
against the running nodes, not assumed. A directory there outlives any number of
rebuilds, which turns the 140 MB download into a genuine one-time cost.

### Why not a static PersistentVolume

A static `local` PV with `nodeAffinity` would express this more conventionally.
It is **cluster-scoped**, and the `apps` AppProject refuses cluster-scoped
resources — correctly, because a PV can declare a `hostPath` to *anywhere* on the
host, which is real privilege escalation rather than a formality. A `hostPath` in
the pod spec asks for exactly one directory and needs no new privilege.

## ⚠️ The passkey data risk, accepted knowingly

**`/srv/opengym/data` on `k3s-worker-2` is the least replaceable data in this
cluster, and it lives on one SD card.**

It holds the WebAuthn credentials, the session secret and the VAPID keypair. If
that card fails, or that node is rebuilt from a fresh image, **every account is
gone and cannot be recovered** — a passkey is a keypair held by the
authenticator and registered against this server's copy. Lose the server's copy
and there is nothing to re-issue, nothing to reset, and no email-recovery path
to fall back on. Everyone re-registers from nothing.

This differs in kind from every other volume here:

| | If lost |
|---|---|
| Prometheus, Grafana | metrics start from empty — annoying |
| openGym **media** | re-downloads, 140 MB — free |
| rh-dashboard archive | statements can be re-uploaded — tedious |
| **openGym data** | **accounts are unrecoverable** |

It is accepted for this version because the instance has one user and the cost of
a rebuild is one re-registration. That calculus changes the moment a second
person has an account, and it is not a risk to leave in place indefinitely.

**Mitigation until the next version:** `/srv/opengym/data` is a few megabytes.
Copying it off the node — into the NAS, or anywhere at all — is the single
highest-value backup in the cluster per byte, and there is currently no backup
of anything.

For the media the same trade is obviously right: losing it costs a re-download,
and local disk serves 140 MB of images far faster than NFS would.

> **Next version: a static NFS PersistentVolume with a declared path.**
>
> It survives rebuilds for a *different* reason than `/srv` does — because the
> path is written in git, a rebuilt cluster reattaches it instead of provisioning
> an empty directory. That is the property dynamic NFS provisioning lacks. It
> also removes the single-card dependency, and `/data` is a few megabytes, so NFS
> latency is irrelevant to it (unlike the media, which should stay local).
>
> Doing it needs the PV to live somewhere the `apps` AppProject permits — either
> whitelisting `PersistentVolume` there, with the hostPath escalation understood
> and accepted, or an infra-side home for app storage declarations.

Until then, `/srv/opengym/data` is worth including in whatever backup story
arrives first. It is small, and it is the least replaceable data in the cluster.

## Consequences

- **One replica of the api, permanently.** `server.js` writes `db.json` and
  `state-<uid>.json` with plain `writeFileSync` and no locking. Two replicas
  would interleave writes and corrupt user state silently, only under concurrent
  use.
- **Both pods pinned to `k3s-worker-2`.** If that node is down, openGym is down.
- **No secrets.** The session secret and VAPID keypair are generated into the
  data directory at 0600 on first start. Nothing to encrypt, nothing to rotate —
  the shape [ADR 0014](0014-sops-as-the-only-secret-manager.md) prefers.
- **`INVITE_ONLY=0` initially, by design, for a single-user instance.** There is
  no bootstrap admin and no way to issue an invite before an admin exists, so the
  first account has to be created by whoever reaches the app first. On a tailnet
  with one member that window is closed by the network itself.

  The intended sequence, and it is not optional:

  1. Deploy, then **register immediately** at
     `https://opengym.mongoose-galaxy.ts.net` — this creates the only account.
  2. Read the resulting uid and set `ADMIN_UIDS` to it.
  3. Set `INVITE_ONLY=1`.

  Until step 3, anyone who can reach the tailnet can create an account. That is
  acceptable for a single-operator tailnet and would not be for anything wider.

  **Next version: seed the first admin from the secret manager instead.** A
  bootstrap credential supplied at deploy time removes the open-registration
  window entirely, rather than closing it by hand afterwards. That needs a
  secret manager, which this cluster does not currently have
  ([ADR 0014](0014-sops-as-the-only-secret-manager.md)) — a working
  implementation is parked on `feature-openbao`, and admin bootstrap is a
  concrete consumer for it, which is precisely the bar ADR 0014 set for bringing
  it back.
- **The media download clones a third-party repo at `--depth 1` of a moving
  branch.** It only runs when the directory is empty, so in practice once — but
  it is unpinned, and pinning it to a commit would be an improvement.
