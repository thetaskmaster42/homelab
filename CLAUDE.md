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
- **All persistent storage is NFS on `portal` (192.168.11.3).** `nfs` is the
  default and only StorageClass; k3s runs `--disable=local-storage`, so
  `local-path` does not exist. A PVC naming no class lands on the NAS. This
  makes `portal` a hard dependency of every stateful workload — see
  [ADR 0006](docs/decisions/0006-nfs-default-storage.md) before moving anything
  back to local disk.

## Architecture

### The CLI/ArgoCD boundary

```
clusters/rps/cluster.yaml → homelab CLI → k3s + Calico + ArgoCD + bootstrap secrets
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

- **Calico (CNI)** — installed by the CLI. No pod, ArgoCD included, schedules
  without a CNI. This chicken-and-egg is permanent.
- **ArgoCD itself** — something has to run the first install. `homelab bootstrap`
  does, and is idempotent so it doubles as break-glass recovery.
- **The age private key and Tailscale OAuth client** — they cannot live in the
  repo they unlock. The CLI applies them at bootstrap.

k3s is installed with `--flannel-backend=none --disable-network-policy
--disable=servicelb --disable=traefik`. Calico, MetalLB and a GitOps-managed
Traefik replace those. Don't re-enable the bundled versions — two controllers
fighting over one Deployment is the failure mode.

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

## Conventions

- Comment the *why* on any non-default setting. Every manifest here does; the
  reason a flag exists is the expensive thing to rediscover.
- Bash: `#!/bin/bash`, `set -euo pipefail`, `cd "$(dirname "$0")"`.
- CLI step functions **return command strings** rather than executing them, so
  the interesting logic is unit-testable with no SSH and no cluster. Preserve
  that shape when adding steps.
- Prefer editing an existing service's `values.yaml` over adding CLI flags or
  new abstractions.
