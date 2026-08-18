# 0001 — ApplicationSet git generator, not app-of-apps or an umbrella chart

**Status:** accepted, 2026-08-17

## Context

The requirement is that infra services be config-driven: adding or retiring one
should be a config edit, not a hand-written ArgoCD `Application` per service.
Three mechanisms were considered.

## Options

**(a) ApplicationSet with a git files generator.** One directory per service;
the controller generates one Application per `service.yaml` it finds.

**(b) The CLI templates Application YAML from config.** The `homelab` CLI reads
a services config and writes out Application manifests.

**(c) An umbrella Helm chart** whose `values.yaml` lists services, rendering one
Application each.

## Decision

**(a).**

(b) was rejected because it puts the laptop in the Day-1 path: every infra change
would require running a local binary, and the generated YAML would be committed
alongside the config that generated it — two representations of the same truth,
which drift. It also blurs the CLI/ArgoCD boundary that the rest of the design
depends on.

(c) is the closest runner-up and genuinely attractive: one values file really is
the purest "single config edit", and because umbrella-rendered Applications are
resources *of the parent Application*, they would honour sync waves — which (a)
cannot. It was rejected because per-service Helm values become deeply nested
blobs inside one file (kube-prometheus-stack's values alone are substantial),
per-service SOPS files stop being natural, and a YAML error in one service breaks
the sync of every service. It also means maintaining a chart.

(a) keeps the blast radius per-service, makes deletion prune cleanly and
automatically, keeps values and secrets as ordinary per-service files, and is
about forty lines of YAML in total.

## Consequences

- Ordering between services **cannot** be expressed with sync waves; see
  [0002](0002-retry-not-sync-waves.md).
- The `resources-finalizer.argocd.argoproj.io` finalizer becomes mandatory in the
  template, or deletion orphans workloads.
- Adding a chart from a new upstream repository requires one line in the `infra`
  AppProject's `sourceRepos`.
