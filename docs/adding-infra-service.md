# Adding (and retiring) an infrastructure service

Infrastructure services are Helm charts. There is no other kind. If something
cannot be expressed as a chart, it either belongs in `apps/` or it belongs in
the CLI's bootstrap path.

## Add

Create one directory with two files.

`infra/services/<name>/service.yaml` — the generator input:

```yaml
name: <name>                # must match the directory name
namespace: <namespace>      # created automatically
extraManifests: "false"     # "true" if you also add a manifests/ directory
chart:
  repo: https://charts.example.com
  name: <chart-name>
  version: "1.2.3"          # pinned. CI rejects '*' and 'latest'.
```

`infra/services/<name>/values.yaml` — ordinary Helm values.

Then add the chart repository to the `infra` AppProject's `sourceRepos` in
`argocd/registry/projects.yaml`, or ArgoCD will refuse the source. This is the
only file outside your service directory that ever needs touching, and only for
a chart repo that has never been used before.

Push. `appset-infra` notices the new `service.yaml`, generates an Application,
and syncs it.

### If the chart ships CRDs you need to instantiate

Set `extraManifests: "true"` and add `infra/services/<name>/manifests/`
containing the custom resources. A companion Application named `<name>-config`
is generated for that directory.

The split exists because a CR cannot be applied before its CRD exists, and the
CRD arrives with the chart. The companion Application retries every 30s
indefinitely until the CRD lands. **Do not try to order the two with sync
waves** — waves only order resources within a single Application, and these are
two separate Applications with no parent sync to sequence them.

`metallb` and `cert-manager` are the worked examples.

## Retire

```sh
git rm -r infra/services/<name>
git push
```

The git generator stops producing the parameters, the ApplicationSet controller
deletes the Application, and the `resources-finalizer.argocd.argoproj.io`
finalizer cascades the delete to every resource it owned — including the
namespace, if `CreateNamespace=true` made it.

**PersistentVolumeClaims are the exception you must think about.** They are
deleted along with the namespace. If the data matters, back it up first or
annotate the PVC with `argocd.argoproj.io/sync-options: Prune=false` before
retiring.

## Before you push

```sh
make validate
```

This renders the chart with your values, schema-checks the output, confirms the
version is pinned, and — the important one on this cluster — verifies every
image in the rendered output publishes a `linux/arm64` manifest. An amd64-only
image fails here in seconds instead of `CrashLoopBackOff`-ing on a Pi with
`exec format error`, which is a genuinely confusing failure to debug.
