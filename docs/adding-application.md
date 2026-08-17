# Adding an application

Applications are homebuilt projects. The split is deliberate:

| Layer | Lives in | Why |
|---|---|---|
| Source, Dockerfile, tests, CI | the app's own repo | it is the app's business |
| Kubernetes base (Deployment/Service/PVC) | the app's own repo, `k8s/` | ships with the code that needs it |
| Image pin, ingress, exposure, registration | **here**, `apps/<name>/` | it is the cluster's business |

The registration here is what makes the dashboard a complete picture even though
the code lives elsewhere.

## In the app's repo

Build a **linux/arm64** image and push it to `ghcr.io`. The cluster has no amd64
node, so an amd64-only image will never schedule.

Use GitHub's native ARM runners (`runs-on: ubuntu-24.04-arm`), which are free for
public repositories and roughly an order of magnitude faster than QEMU
emulation. Tag images with the commit sha — never rely on `:latest`, which makes
rollback impossible and hides what is actually running.

Keep the Kubernetes manifests in `k8s/`. Leave `metadata.namespace` out; the
overlay sets it.

## Here

`apps/<name>/app.yaml`:

```yaml
name: <name>
namespace: <namespace>
sourceRepo: https://github.com/thetaskmaster42/<name>
exposure: tailnet        # tailnet | funnel | internal
```

`apps/<name>/kustomization.yaml` pulls the app repo's manifests **pinned to a
commit sha**, pins the image tag, and layers on patches. See
`apps/prep-tracker/` as the worked example.

Pinning to a sha rather than a branch is what makes the promotion explicit: a
push to the app repo cannot silently change what is deployed. Bumping the ref
here is the deploy, and it shows up as a reviewable diff.

## Exposure

`ingressClassName: tailscale` publishes the app on the tailnet at
`https://<name>.tailcb5a3f.ts.net` with a real Let's Encrypt certificate,
reachable from your devices anywhere and from nobody else's.

Adding `tailscale.com/funnel: "true"` makes it **genuinely public on the
internet**. Before setting it, be explicit about what authentication the app has
— `prep-tracker` deliberately stays on the tailnet because it has none.

## Verify

```sh
kubectl kustomize apps/<name>    # renders, with the image pinned as you expect
make validate
```
