# 0005 — Headlamp over Devtron for the cluster dashboard

**Status:** accepted, 2026-08-17

## Context

The requirement was a single pane showing every node, infrastructure service and
application running on the homelab. Devtron was the preferred candidate: it
markets exactly that, and its dashboard-only mode explicitly reads Helm, ArgoCD
and Flux applications.

Two things had to be true for it to work here.

## Evaluation

**Does it run on arm64? Yes.** This was the expected blocker and it turned out
not to be one. Rendering `devtron-operator` 0.23.3 and checking every image
against its registry showed all twelve publish `linux/arm64` — `dashboard`,
`hyperion`, `kubelink`, `authenticator`, `chart-sync`, `ci-runner`, `dex`,
`migrator`, `postgres`, `postgres_exporter`, `devtron-utils`, `kubectl`.

An earlier check of `kubelink:latest` had found it amd64-only, which looked
disqualifying. But the chart pins `kubelink:09867a9c-564-39289`, and that tag is
multi-arch. Guessing from a floating tag gave the wrong answer; rendering the
chart gave the right one. This is why `tests/test_images_arm64.py` renders rather
than reasons.

**Is it compatible with GitOps? No.** The chart renders nondeterministically.
Two `helm template` runs on identical inputs differ in:

- `postgresql-password` — a fresh random value every render
- four Jobs with random name suffixes, including
  `postgresql-migrate-devtron`, `postgresql-migrate-casbin` and
  `postgresql-create-databases`

None of those Jobs carry a `helm.sh/hook` annotation, so ArgoCD tracks them as
ordinary resources. Under `automated` + `selfHeal` that means ArgoCD would
rewrite the database password while the running Postgres kept the old one, and
would repeatedly create and prune **database migration Jobs**. That is a
correctness problem, not cosmetic drift.

Setting `global.externalPostgres.enabled=true` against the CloudNativePG cluster
removes the bundled Postgres and the rotating password entirely — a genuinely
good fit. But the four Job names still churn, leaving the Application
permanently OutOfSync. A dashboard whose purpose is showing sync health, itself
permanently unhealthy, is self-defeating.

## Decision

Use **Headlamp**, which renders byte-identical across runs and ships a single
`linux/arm64` image. It needs no special-casing: `automated` and `selfHeal`
apply to it exactly as to every other service.

The complete picture is assembled from three panes rather than one:

| Pane | Answers |
|---|---|
| Headlamp | what resources exist, and their live state |
| ArgoCD UI | what git says should exist, and whether it matches |
| Grafana | how it is behaving over time |

## Consequences

- Headlamp is a resource browser, not a CI/CD platform. Devtron's build and
  deploy pipelines are not replaced — but they were never wanted, since ArgoCD
  is the deployer by design.
- Headlamp is extensible via plugins, including one for ArgoCD, so consolidating
  those three panes later is possible and is a good hands-on project.
- Devtron remains viable if the requirement changes. The blocker is its chart's
  determinism, not the platform, and revisiting means re-running
  `make arm64` plus a two-render diff.
