# Bootstrap: bare Pis to a synced cluster

Seven stages. The first six are the CLI's; everything after is git.

## 0. Preflight — `homelab init`

Mutates nothing, exits non-zero on any failure:

- SSH reachable with `BatchMode=yes` (a password prompt in automation is a bug)
- `sudo -n true` succeeds
- `uname -m` is `aarch64` on every node
- Clock skew under 5s — TLS certificate validation fails in confusing ways
  otherwise
- Nothing already listening on 6443
- Disk headroom, noting `k3s-worker-2`'s smaller volume

## 1. k3s server — `homelab install`

Installed on `192.168.11.7` with:

```
--flannel-backend=none        Calico is the CNI
--disable-network-policy      Calico enforces policy instead
--disable=servicelb           MetalLB provides LoadBalancer IPs
--disable=traefik             Traefik is a GitOps-managed Helm chart
--node-ip 192.168.11.7
--tls-san 192.168.11.7
--write-kubeconfig-mode 644
```

There is no local DNS, so `--tls-san` takes the IP rather than a hostname. Add
the future Tailscale hostname as a second SAN now, so the API certificate does
not have to be reissued when the operator's API proxy comes up.

Then fetch `/etc/rancher/k3s/k3s.yaml`, rewrite `server:` to
`https://192.168.11.7:6443`, and merge it as context `rps`.

## 2. Calico — the unavoidable chicken-and-egg

Apply the Tigera operator, then the `Installation` CR with the pod CIDR from
`cluster.yaml`, retrying until the operator's CRDs register. Wait for
`kubectl wait --for=condition=Ready node --all`.

**This cannot be GitOps-managed.** No pod schedules without a CNI, and that
includes ArgoCD. It belongs to the CLI permanently.

## 3. Agents join

In parallel across `192.168.11.6` and `.5`. The token is read live over SSH from
`/var/lib/rancher/k3s/server/node-token` and **never persisted** — this repo is
public, and that token is a cluster-admin credential. The state file records only
a fingerprint of it, which is enough to detect "the server was rebuilt, agents
must rejoin".

## 4. ArgoCD — `homelab bootstrap`

`helm upgrade --install argocd argo/argo-cd -n argocd -f argocd/bootstrap/values.yaml`
at the version pinned in `cluster.yaml`. Also not GitOps: something has to run
the first apply.

`bootstrap` is separate from `install` and idempotent, so it doubles as
break-glass recovery when ArgoCD has broken its own ability to sync.

## 5. Bootstrap secrets

Decrypted with the local age key and applied directly:

1. the age **private** key, so ArgoCD can decrypt everything else in the repo
2. the Tailscale operator OAuth client
3. `grafana-admin` in the `monitoring` namespace (`admin-user`,
   `admin-password`) — kube-prometheus-stack references it rather than
   generating one, because a chart-generated password is regenerated on every
   render and would restart Grafana on every sync
4. a GHCR pull secret, only if a package ever goes private

Each of these exists here rather than in OpenBao for the same reason: they are
needed to bring the platform up, and OpenBao starts sealed.

This is the root of the chain of trust: one key on your laptop unlocks a public
repository full of encrypted values.

## 6. Root Application

Apply `argocd/bootstrap/root.yaml`. It syncs `argocd/registry/`, which brings up
the AppProjects and the three ApplicationSets. From here, git is the only input.

## 7. Convergence

Not a schedule — a set of data dependencies that resolve by retry:

```
metallb ──► metallb-config (IPAddressPool, L2Advertisement)
   └──► traefik (claims 192.168.11.240)
cert-manager ──► cert-manager-config (selfsigned -> CA issuers)
tailscale-operator ──► ingressClass "tailscale"
kube-prometheus-stack
apps/*
```

Each Application retries every 30s indefinitely until its prerequisites exist.
This is order-independent by design: it survives a rebuild in any sequence, and
it removes a whole class of ordering bugs that explicit sequencing would create.

## Teardown — `homelab nuke --yes`

`k3s-uninstall.sh` on the server, `k3s-agent-uninstall.sh` on the agents. These
are different scripts; using the wrong one leaves a broken install behind.

**Application data survives this; Prometheus and OpenBao do not.** PVCs on the
`nfs` class use `reclaimPolicy: Retain`, so a nuke leaves them on `portal`
untouched. Prometheus and OpenBao are on `local-path`
([ADR 0008](decisions/0008-local-disk-for-observability-and-secrets.md)), which
lives under `/var/lib/rancher/k3s/storage` and is erased outright — expect to
re-initialise and re-seed OpenBao, and to start metrics from empty, after every
rebuild. `nuke` lists the PVCs on each side and requires explicit confirmation.

Surviving is not the same as coming back. `Retain` leaves each PV behind in
`Released`, and a rebuilt cluster provisions *fresh* directories rather than
reattaching the old ones. The data sits under the export on `portal` until you
either point a new PVC at it with a hand-written PV, or delete it to reclaim the
space. Nothing does that automatically, by design.
