# cluster/ — GitOps source of truth

Everything that runs on the k3s cluster is declared here and synced by
**ArgoCD**. Nothing under `infrastructure/` or `apps/` is applied by hand.

```
bootstrap/        One-time: install ArgoCD, apply the root apps
infrastructure/   Cluster addons — one ArgoCD Application per addon
apps/             End-user applications — one ArgoCD Application per app
```

## How it works (app-of-apps)

1. `bootstrap/install.sh` installs ArgoCD and applies `bootstrap/root-apps.yaml`.
2. `root-apps.yaml` defines two ArgoCD Applications that watch this repo:
   - **infrastructure** → syncs every manifest under `cluster/infrastructure/`
   - **apps** → syncs every manifest under `cluster/apps/`
3. Those directories contain ArgoCD `Application` manifests themselves, so
   adding a service = committing one YAML file. ArgoCD picks it up and deploys.

Only the **top level** of `infrastructure/` and `apps/` is synced by the root
apps. Subdirectories (e.g. `infrastructure/metallb-config/`) hold raw
manifests deployed by their own companion Application — this keeps CRDs and
the resources that depend on them in separately-retried syncs.

## Adding a service

1. Create `cluster/apps/<name>.yaml` (or `infrastructure/<name>.yaml` for an
   addon) — an ArgoCD Application pointing at a Helm chart with inline values,
   or at a manifests directory in this repo.
2. Commit and push. ArgoCD syncs it.
