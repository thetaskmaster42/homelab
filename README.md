# Homelab

Infrastructure-as-code for a three-node arm64 k3s cluster running on Raspberry
Pis. Everything that runs on the cluster is declared in this repo and reconciled
by ArgoCD.

The cluster is deliberately disposable. Destroying and rebuilding it is a
first-class, exercised operation — not a disaster recovery scenario.

## The two planes

The repo's top-level layout *is* the architecture:

```
clusters/    Node inventory — the config the CLI consumes
cli/         The `homelab` CLI: bare Pis -> a running ArgoCD
argocd/      The GitOps control plane: AppProjects + ApplicationSets
infra/       Infrastructure services. Helm charts only.
apps/        Homebuilt applications. Source lives in their own repos.
docs/        Architecture, runbooks, decision records
tests/       Static validation of everything above
```

**Infrastructure** is cluster plumbing — load balancing, ingress, certificates,
networking, observability. Every infra service is a Helm chart, defined by two
files, and is added or retired by editing config.

**Applications** are homebuilt projects. Their code, Dockerfile and CI live in
their own repositories; what lives here is the registration and the deployment
overlay. That registration is what keeps the dashboard a complete picture even
though the code is elsewhere.

## The boundary

```
clusters/rps/cluster.yaml
        │
        ▼
   homelab CLI ──SSH──► k3s + Calico + ArgoCD + bootstrap secrets
        │
        │  (the CLI's job ends here, permanently)
        ▼
     ArgoCD ──► infra/services/*  and  apps/*
```

The CLI owns only what must exist *before* the Kubernetes API is usable. It
never installs a service and never applies an application manifest. Once ArgoCD
is running, **git is the only input** — a push is a deploy.

## Adding an infrastructure service

Create a directory with two files and push:

```
infra/services/<name>/service.yaml    # chart repo, name, pinned version, namespace
infra/services/<name>/values.yaml     # Helm values
```

An ApplicationSet notices the directory and generates an ArgoCD Application for
it. Retiring the service is `git rm -r` on the directory — the Application is
deleted and its resources are pruned. See
[docs/adding-infra-service.md](docs/adding-infra-service.md).

## Adding an application

```
apps/<name>/app.yaml            # name, namespace, source repo, exposure
apps/<name>/kustomization.yaml  # remote base pinned to a sha, image pin, patches
```

See [docs/adding-application.md](docs/adding-application.md).

## Bootstrap

```sh
uv run homelab init      # preflight: SSH, sudo, arch, disk, clock. Mutates nothing.
uv run homelab install   # k3s server -> Calico -> agents join
uv run homelab bootstrap # ArgoCD + bootstrap secrets + the root Application
uv run homelab status    # node health + ArgoCD app sync state
uv run homelab nuke --yes  # tear it all down
```

Full sequence and its ordering constraints: [docs/bootstrap.md](docs/bootstrap.md).

## Conventions

- **Chart versions are pinned.** No `targetRevision: '*'`. CI rejects it.
- **Every image must have a `linux/arm64` manifest.** There is no amd64 node.
  CI checks this against rendered manifests, so an amd64-only image fails a PR
  rather than CrashLooping on a Pi.
- **This repository is public.** Secrets come in two tiers: SOPS+age for the
  bootstrap credentials that must live in git, OpenBao for what applications
  read at runtime. CI fails on any plaintext secret. See
  [ADR 0004](docs/decisions/0004-two-tier-secrets.md).
- **Applications share one PostgreSQL**, run by the CloudNativePG operator in
  the `databases` namespace — a database and role per app, not a database per
  app.
- **Storage splits by whether the data can be rebuilt.** Application and database
  data goes on `nfs` (the `portal` NAS) so it survives node loss and
  `homelab nuke`. The monitoring stack and OpenBao stay on node-local `local-path`,
  because they are reconstructible and are precisely what must keep working when
  the NAS does not. Both classes are marked default, so **every PVC names
  its class explicitly** and CI enforces it. See
  [ADR 0006](docs/decisions/0006-nfs-default-storage.md) and
  [ADR 0008](docs/decisions/0008-local-disk-for-observability-and-secrets.md).
- Comment the *why* on non-default settings. The reason a flag is set is the
  part that is expensive to rediscover.

## Hardware

Three Pi 16 GB nodes on `192.168.11.0/24`, plus a NAS and a gateway Pi. See
[docs/hardware.md](docs/hardware.md) — including the disk asymmetry that
constrains where stateful workloads can run.
