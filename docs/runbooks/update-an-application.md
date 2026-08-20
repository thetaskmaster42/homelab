# Runbook: updating an application to a new build

Promotion is a git change, never a `kubectl apply`. Bumping the pinned refs in
`apps/<name>/kustomization.yaml` and merging to `main` *is* the deploy; ArgoCD
does the rest. Applying by hand produces a resource ArgoCD immediately reverts,
and leaves git describing something that is not running.

Worked example: `prep-tracker` `40754fd` → `80a06c8`, the release that moved it
from SQLite to PostgreSQL.

---

## 1. Check what actually changed upstream

Do this first. It decides whether the update is a one-line edit or a restructure.

```sh
gh api repos/thetaskmaster42/prep-tracker/compare/<old-sha>...<new-sha> \
  --jq '.files[]? | select(.filename|startswith("k8s/")) | "\(.status)  \(.filename)"'
```

Empty output means only the application code changed — bump the image tag and go.

Any output means the deployment shape changed, and **bumping the image tag alone
is wrong**: the new image would run against the old manifests. Here it printed:

```
modified  k8s/deployment.yaml
added     k8s/migrate-job.yaml
added     k8s/postgres-cluster.yaml
removed   k8s/pvc.yaml
```

A removed `pvc.yaml` and an added database is a storage migration, not a release.

## 2. Verify the image exists and is arm64

Every node is arm64. An amd64-only image passes review and then
`CrashLoopBackOff`s with `exec format error`.

```sh
docker manifest inspect ghcr.io/thetaskmaster42/prep-tracker:<tag> \
  | grep -o '"architecture": "[a-z0-9]*"' | sort -u
```

`make arm64` checks this across everything, and CI blocks a PR that fails it.

## 3. Back up anything about to be discarded

If a PVC disappears from the manifests, ArgoCD prunes it and the data goes with
it. Copy it out **before** merging — afterwards there is nothing to copy.

```sh
POD=$(kubectl -n interview get pod -l app=prep-tracker -o jsonpath='{.items[0].metadata.name}')
mkdir -p ~/prep-tracker-backups
kubectl -n interview cp "$POD:/data/prep_tracker.db" \
  ~/prep-tracker-backups/prep_tracker-$(date +%Y%m%d-%H%M%S).db

file ~/prep-tracker-backups/*.db          # confirm it is not a 0-byte artifact
```

A schema migration creates tables; it does not carry rows across from a
different database engine. Assume the old data is gone unless you moved it.

## 4. Update the overlay

Pin **every** resource ref to the new sha, add what appeared, drop what was
removed. Use the full 40-character sha in URLs — CI rejects a branch ref, because
a branch means a push to the app repo silently changes what runs here.

```sh
gh api repos/<owner>/<repo>/commits/<short-sha> --jq '.sha'    # full sha
```

The `images:` entry keeps the short tag and applies to every container in the
overlay, so the migration Job and the Deployment run identical code. A migration
executed by a different build than the app is a bad surprise.

## 5. Render and validate before pushing

```sh
kubectl kustomize apps/prep-tracker          # what will actually be applied
make validate                                # schema, pinning, arm64, secrets, lint
```

Read the render, do not skim it. Confirm the image tag, replica count, and that
nothing you expected has silently vanished.

## 6. Merge — and let ArgoCD do the deploy

```sh
git add apps/prep-tracker/kustomization.yaml
git commit -m "prep-tracker: <old> -> <new>"
git push
gh pr create --base main --head <branch> --title "..." --body "..."
gh pr checks <n> --watch
gh pr merge <n> --merge
```

ArgoCD polls `main`, so it picks the change up on its own. To avoid waiting, or
when it is serving a cached render, force a refresh — this is still not a manual
apply, it only tells ArgoCD to re-read git:

```sh
kubectl -n argocd annotate app prep-tracker argocd.argoproj.io/refresh=hard --overwrite
```

## 7. Verify against the new commit, not just "Synced"

`Synced` alone does not mean synced to *your* commit — it may be Synced to the
previous one. Check the revision:

```sh
kubectl -n argocd get app prep-tracker \
  -o jsonpath='{.status.sync.status} {.status.health.status} {.status.sync.revision}{"\n"}'

kubectl -n interview get pods -o jsonpath='{range .items[*]}{.spec.containers[0].image}{"\n"}{end}'
curl -sS -o /dev/null -w "%{http_code}\n" https://prep-tracker.mongoose-galaxy.ts.net/
```

For **multi-source** Applications (every infra service) `.status.sync.revision`
is empty by design; use `.status.sync.revisions` instead.

If a PreSync hook Job is present, it must complete before the rollout begins:

```sh
kubectl -n interview get jobs
kubectl -n interview logs job/prep-tracker-migrate
```

## When a sync stalls

This promotion hit four blockers in sequence. Each looked like a different
problem and all four came from the same root: ArgoCD was mid-operation on the
*previous* manifests.

### `CreateContainerConfigError: secret "<cluster>-app" not found`

A PreSync hook that needs a database. PreSync runs **before** the main sync, but
the secret is published by the CloudNativePG operator only once the `Cluster`
exists — and the Cluster is a main-sync resource. The hook waits forever for
something it is itself preventing.

Fix by ordering with sync waves instead of a hook. Waves **do** order resources
within a single Application (unlike across Applications, where they are inert):

```yaml
# Cluster   wave 0 (default) -> operator publishes the secret
# migration wave 1
# Deployment wave 2
```

Add `argocd.argoproj.io/sync-options: Replace=true` to the Job — a Job spec is
immutable, so a changed migration cannot be patched in place.

### A Job stuck in `Terminating` forever

```sh
kubectl -n <ns> get job <name> -o jsonpath='{.metadata.finalizers}'
# ["argocd.argoproj.io/hook-finalizer"]
```

ArgoCD holds hook resources with a finalizer. Once the hook annotation is
removed, nothing is left to clear it, so the old Job hangs:

```sh
kubectl -n <ns> patch job <name> --type merge -p '{"metadata":{"finalizers":null}}'
```

### The sync keeps using the OLD manifests

The clearest tell — check which revision the running operation is pinned to:

```sh
kubectl -n argocd get app <name> -o jsonpath='{.operation.sync.revision}'
```

An in-flight operation is pinned to the revision it started with. Merging a fix
does nothing while it runs, and `refresh=hard` does not interrupt it. Terminate
it by removing the field:

```sh
kubectl -n argocd patch app <name> --type json -p '[{"op":"remove","path":"/operation"}]'
kubectl -n argocd annotate app <name> argocd.argoproj.io/refresh=hard --overwrite
```

### `waiting for deletion of PersistentVolumeClaim/...`

A pruned PVC cannot be deleted while a pod still mounts it, and that pod is only
replaced in a later wave — which the prune is blocking. Release it by scaling the
old Deployment to zero; ArgoCD restores the replica count from git on the next
wave:

```sh
kubectl -n <ns> scale deploy/<name> --replicas=0
```

**None of these are manual deploys.** They unblock a stuck operation so ArgoCD
can apply what git already says. The distinction that matters: never
`kubectl apply` a manifest, because ArgoCD would revert it and git would then
describe something that is not running.

## 8. Rolling back

Revert the commit. The old sha is still pinned in git history, the old image is
still in the registry, and ArgoCD converges the same way it did forwards.

```sh
git revert <commit> && git push
```

Rolling back **code** is easy. Rolling back a **schema migration** is not — that
needs a down-migration or a restore, which is why step 3 matters.
