# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

Infrastructure-as-code for a three-node **arm64** k3s cluster on Raspberry Pis.
There is no application code here — the deliverables are YAML manifests, Helm
values, and a Python CLI. "Running" a change to anything under `infra/` or
`apps/` means pushing to `main`; ArgoCD reconciles from git, so a local edit is
inert until committed and pushed.

The cluster is intentionally disposable. `homelab nuke && homelab install` is a
routine drill, not an emergency. Prefer designs that survive being destroyed.

## Non-negotiable facts

- **Every node is arm64.** There is no amd64 node and there will not be one. Any
  image without a `linux/arm64` manifest will `CrashLoopBackOff` with
  `exec format error`. The dev laptop is x86_64 — never assume an image that
  runs locally will run on the cluster.
- **This GitHub repo is public.** Never commit a plaintext credential. Secrets
  are SOPS-encrypted with age.
- **Chart versions are pinned.** `targetRevision: '*'` is banned; CI rejects it.
- **Two StorageClasses, and TWO defaults.** `nfs` (on `portal`, 192.168.11.3)
  holds application data; k3s's `local-path` holds the monitoring stack, which
  must survive a NAS outage. k3s marks `local-path` default and cannot be stopped
  from doing so, so a PVC naming no class resolves *arbitrarily*. **Every PVC
  must name its class explicitly** — `tests/test_apps.py` fails the build
  otherwise. See [ADR 0006](docs/decisions/0006-nfs-default-storage.md) and
  [ADR 0008](docs/decisions/0008-local-disk-for-observability-and-secrets.md).

## Architecture

### The CLI/ArgoCD boundary

```
clusters/rps/cluster.yaml → homelab CLI → k3s (+ its flannel) + ArgoCD + bootstrap secrets
                                              ↓  (CLI's job ends here)
                                           ArgoCD → infra/services/* and apps/*
```

The CLI owns only what must exist before the Kubernetes API is usable: k3s, the
CNI, ArgoCD itself, and the secrets that cannot live in the repo they decrypt.
It never runs `helm install` for a service and never applies an app manifest. If
you are tempted to add a per-service deploy step to the CLI, that is a sign the
change belongs in `infra/services/` instead.

### How config-driven infra actually works

`argocd/bootstrap/root.yaml` is the single root Application. It syncs only
`argocd/registry/`, which holds two AppProjects and three ApplicationSets. Those
ApplicationSets use **git files generators** to produce one Application per
directory:

| ApplicationSet | Generator path | Produces |
|---|---|---|
| `appset-infra` | `infra/services/*/service.yaml` | one Helm Application per service |
| `appset-infra-config` | same, filtered on `extraManifests: "true"` | a companion Application for raw CRs |
| `appset-apps` | `apps/*/app.yaml` | one kustomize Application per app |

So adding a service is *creating a directory*, and retiring one is `git rm -r`.
Nothing in `argocd/` changes either way. That is the whole point — don't add
per-service entries to the registry.

Infra Applications are **multi-source**: the chart comes from its upstream repo
while `values.yaml` comes from this repo via the `$values` ref. This is what lets
"Helm charts are mandatory" and "config lives in git" both be true.

### Two things that will bite you

1. **Sync waves do not order Applications.** Waves order resources *within* one
   Application's sync. ApplicationSet-generated Applications have no parent sync
   operation, so a `sync-wave` annotation on them is inert. Ordering is achieved
   by `retry: {limit: -1}` — a CR Application simply retries until its CRD
   exists. Do not try to reintroduce wave-based ordering across services.
2. **The `resources-finalizer.argocd.argoproj.io` finalizer is load-bearing.**
   Without it in the ApplicationSet template, deleting a service directory
   removes the Application but *orphans* its workloads — a running service with
   no git representation. Never remove it.

### What cannot be GitOps-managed

- **The CNI** — nothing installs it. Flannel is bundled inside the k3s binary
  and running before the API server serves its first request, so there is no
  ordering problem to solve (see
  [ADR 0011](docs/decisions/0011-flannel-over-calico.md)).
- **ArgoCD itself** — something has to run the first install. `homelab bootstrap`
  does, and is idempotent so it doubles as break-glass recovery.
- **The age private key and Tailscale OAuth client** — they cannot live in the
  repo they unlock. The CLI applies them at bootstrap.

k3s is installed with `--flannel-backend=vxlan --disable=servicelb
--disable=traefik`. MetalLB and a GitOps-managed Traefik replace the bundled
versions — don't re-enable those, two controllers fighting over one Deployment
is the failure mode. Flannel is the exception: it IS the bundled one, and is
used as such.

**Never add `--disable-network-policy`.** It disables k3s's kube-router, which
is what enforces NetworkPolicy here — leaving the ArgoCD chart's six policies in
the API with nothing enforcing them. No error, no event: a policy that fails
open. Verified enforced over TCP; note that kube-router allows ICMP echo
unconditionally, so `ping` cannot be used to test it.

## How changes get made

**Every change goes on a branch and merges through a pull request.** No direct
commits to `main`, including one-line fixes and documentation.

```sh
git checkout -b feature/<thing>      # or fix/, chore/
# ... work, with `make validate` green ...
gh pr create --fill
```

This repo pushes to a live cluster the moment `main` moves — ArgoCD reconciles
from it — so `main` is not a working area. A PR is the only place a change can
be looked at before it is deployed.

## Network facts that cost real debugging time

- **`ping` cannot test a MetalLB L2 VIP.** MetalLB does not answer ICMP for the
  VIP, so a VIP that is serving traffic perfectly still fails `ping`. Test with
  TCP. This is the same trap as kube-router's unconditional ICMP allow above —
  twice now, ping has produced a confident wrong answer here.
- **DHCP leases on this network are `LIFETIME=infinity`.** Clients never re-ask,
  so changing the DHCP DNS option changes nothing until each client is forced to
  renew (`networkctl renew <if>` on the nodes, `nmcli connection up <conn>` on
  the laptop — `nmcli device reapply` is NOT enough, it reuses the cached lease).
  A correct DNS change will look completely broken until you do this.
- **Pi-hole defaults to `listeningMode LOCAL`**, answering only its own subnet.
  Pointing another VLAN at it produces a TCP *connection reset* — the port is
  open, the query is refused. Needs `dns.listeningMode ALL`.
- **A `Job` cannot be `kubectl replace`d.** The controller generates the
  immutable `spec.selector` at admission and the manifest has none, so ArgoCD's
  `Replace=true` alone fails *every* sync after the first with
  `spec.selector: Invalid value: null: field is immutable`. It needs
  `Replace=true,Force=true` (delete + recreate). Worse, it fails at its sync
  wave, and ArgoCD will not start later waves — so the app silently stops
  receiving *any* change while still reporting Healthy.

## Node health

Every node runs `netsnap-sentinel` (see `scripts/README.md`), deployed by
`homelab install`. It probes its peers — rendered from `cluster.yaml` into a
systemd drop-in — captures evidence on failure, then escalates recovery
cheapest-first: `ip neigh flush` → `ethtool -r` → `ip link down/up` → reboot,
logging which stage restored traffic.

Two rules when touching it. **Capture must happen before any recovery**, because
a link bounce resets the interface counters that explain the failure. And
`netsnap-preboot.service` is enabled but must **never** be restarted by
`install` — re-running it rotates real pre-outage evidence out of the two-deep
history. `NETSNAP_DIR` redirects all output, so the capture path is testable
without rebooting a node.

The open investigation is a link-up-but-no-traffic blackhole: `Link detected:
yes` at 1Gb/s, zero error counters, kernel silent, ARP to the gateway
`INCOMPLETE`. Power and thermal are **excluded** (`throttled=0x0`, 43.9 °C), as
are Calico and EEE. Note that a link bounce resets the Pi's MAC/PHY, the ARP
cache *and* the switch's port state simultaneously — so "a bounce fixed it"
attributes nothing, which is why recovery is staged.

## Commands

There is no build. Everything is validation or cluster operations.

```sh
uv run homelab init | install | bootstrap | status | nuke --yes
uv run pytest                       # CLI unit tests
make validate                       # yamllint + helm render + kubeconform + arm64 + sops
kubectl kustomize apps/<name>       # render one app overlay
kubectl -n argocd get applications  # what ArgoCD thinks is deployed
```

Exposure is via Tailscale (tailnet `mongoose-galaxy.ts.net`), not NodePorts or
port forwarding. `ingressClassName: tailscale` gives a private tailnet host; the
annotation `tailscale.com/funnel: "true"` makes it genuinely public — which for
an app with no authentication is a decision, not a detail.

Services are **also** reachable on the LAN at `<name>.rps-home.com` via a second
Ingress (`<name>-lan`, class `traefik`, cert from the `homelab-ca` chain). The
tailnet Ingress is untouched; both target the same Service and which one you get
depends on where you are. Adding LAN access to a service is one new file plus a
line in its kustomization — see [ADR 0016](docs/decisions/0016-lan-ingress.md).

Two services are deliberately excluded and should stay that way. **headlamp** is
an unauthenticated cluster-admin console, so a LAN Ingress would hand cluster
admin to every device on the subnet. **opengym** binds its WebAuthn RP ID to the
tailnet hostname; a second origin has no credentials, and changing `RP_ID`
permanently invalidates every existing passkey.

**journiv** is excluded only until its signup is closed. It has real
authentication but no way to create the first account except the public signup
form, so `DISABLE_SIGNUP` starts `false` and the one-member tailnet is what
closes that window — the LAN would not. Register, set `DISABLE_SIGNUP=true`,
then add the Ingress. See [ADR 0017](docs/decisions/0017-journiv.md).

All LAN names share one MetalLB VIP (192.168.11.240) and are routed by hostname,
so **browsing to the bare IP correctly returns 404** — that is the mechanism
working, not a broken deploy. Pi-hole (192.168.11.2) answers the wildcard
`*.rps-home.com`. Grafana and ArgoCD each pin a single absolute base URL
(`root_url`, `global.domain`) to the tailnet name: their UIs use relative paths
so LAN browsing works, but absolute links they *generate* still point at
`ts.net`. Neither supports two base URLs.

## Conventions

- Comment the *why* on any non-default setting. Every manifest here does; the
  reason a flag exists is the expensive thing to rediscover.
- Bash: `#!/bin/bash`, `set -euo pipefail`, `cd "$(dirname "$0")"`.
- CLI step functions **return command strings** rather than executing them, so
  the interesting logic is unit-testable with no SSH and no cluster. Preserve
  that shape when adding steps.
- Prefer editing an existing service's `values.yaml` over adding CLI flags or
  new abstractions.
