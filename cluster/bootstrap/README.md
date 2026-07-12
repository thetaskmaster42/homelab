# Bootstrap

One-time setup after the k3s cluster exists: install ArgoCD, then hand it
control of the repo.

```sh
# kubeconfig pointing at the k3s cluster
./install.sh
```

What it does:

1. Installs ArgoCD via Helm into the `argocd` namespace (`values.yaml`)
2. Applies `root-apps.yaml` — the two app-of-apps Applications
   (`infrastructure`, `apps`) that watch this repo

From then on, all changes go through git.

## Getting into the UI

```sh
kubectl -n argocd get secret argocd-initial-admin-secret \
  -o jsonpath='{.data.password}' | base64 -d
kubectl -n argocd port-forward svc/argocd-server 8080:443
```

(Ingress + a proper `argocd.rps-home.com` record come later via
`infrastructure/`.)
