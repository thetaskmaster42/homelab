# 0016 — LAN access on rps-home.com, alongside the tailnet

Date: 2026-09-01
Status: Accepted

## Context

Every service was reachable only over the tailnet. That is correct for access
from outside the house, but it makes a device on the home network route out to
Tailscale's coordination and back in to reach a machine three metres away, and
it means anything not enrolled in the tailnet — a guest laptop, a TV, a phone
with the client off — cannot reach the cluster at all.

We own `rps-home.com`. Traefik already holds a MetalLB VIP at `192.168.11.240`.
The private CA chain from ADR 0003 (`selfsigned-root` → `homelab-ca`) had been
built and had no consumer.

## Decision

Each exposed service gets a **second** Ingress, `<name>-lan`, on
`<name>.rps-home.com`, class `traefik`, with a certificate issued by
`homelab-ca`. The tailnet Ingress is untouched and remains the path from
outside.

Both Ingresses target the same Service. Which one you reach depends only on
where you are:

| | hostname | certificate | reachable from |
|---|---|---|---|
| tailnet | `<name>.mongoose-galaxy.ts.net` | Let's Encrypt, via Tailscale | anywhere |
| LAN | `<name>.rps-home.com` | `homelab-ca` | `192.168.11.0/24` |

They are separate files, not extra rules on one Ingress. The two have different
classes, different certificate authorities and different audiences, and merging
them would hide that a change to one does not affect the other.

### Why host-based routing on one VIP

Traefik holds a single MetalLB address, and every LAN service shares it. The
alternative — giving each service its own address from the pool, or making one
service Traefik's default backend so it answers on the bare IP — was rejected.
A default backend would let exactly one service be reached at `192.168.11.240`
and permanently block the others from sharing it.

The consequence is that **the bare IP is not a usable entrypoint**. Browsing to
`https://192.168.11.240` sends `Host: 192.168.11.240`, which matches no rule, and
Traefik correctly returns 404. This looks like a broken deployment and is not;
it is the mechanism working. Access requires the hostname to resolve.

### DNS

The Pi-hole on the Gateway Pi will answer `*.rps-home.com → 192.168.11.240` with
a single wildcard record. Until it does, each client needs a `/etc/hosts` line
per service — `/etc/hosts` has no wildcards, which is precisely why the wildcard
record is the real answer rather than a convenience.

### Certificates

`homelab-ca` is not a public CA, so every device must trust the root once or the
browser warns on every visit. One root covers every LAN service and is valid to
2036:

```sh
kubectl -n cert-manager get secret homelab-root-ca \
  -o jsonpath='{.data.tls\.crt}' | base64 -d > homelab-root-ca.crt
sudo cp homelab-root-ca.crt /usr/local/share/ca-certificates/
sudo update-ca-certificates
```

Firefox and Chrome keep their own trust stores and need the file imported under
Settings → Certificates → Authorities separately from the system store.

`rps-home.com` is a domain we own, so a real Let's Encrypt certificate via DNS-01
is possible later and would delete this step entirely. It needs a DNS provider
API token, which is a secret this cluster does not hold yet. The private CA is
the version that works today with nothing to store.

## What is exposed, and what is not

| Service | LAN Ingress | Why |
|---|---|---|
| drawio | yes | |
| excalidraw | yes | |
| prep-tracker | yes | wave 3, so exposure never blocks the workload |
| rh-dashboard | yes | |
| grafana | yes | absolute-link caveat below |
| argocd | yes | authenticates; widens exposure — see below |
| **headlamp** | **no** | **unauthenticated cluster-admin UI** |
| **opengym** | **no** | **passkeys are bound to the hostname** |

**headlamp** is deliberately excluded. It is a cluster-admin console with no
login, and the tailnet is what stands between it and the world. A LAN Ingress
would hand cluster-admin to every device on `192.168.11.0/24`, including guest
devices and IoT. This is the one exclusion that is a security decision rather
than a technical one, and it should not be reversed casually.

**opengym** is excluded for a different reason: WebAuthn binds the Relying Party
ID cryptographically into every credential at registration. `RP_ID` is
`opengym.mongoose-galaxy.ts.net`, so passkeys registered there **cannot** be used
on `opengym.rps-home.com` — the browser will not even offer them. Adding a LAN
hostname does not extend access, it creates a second origin with no credentials,
and changing `RP_ID` permanently invalidates every existing passkey. See ADR 0015.

## Consequences

**Absolute-URL services degrade partially.** Grafana's `root_url` and ArgoCD's
`global.domain` are each a single absolute URL, pinned to the tailnet name. Both
serve their UI on relative paths, so browsing over the LAN works — but anything
either renders as an absolute link (Grafana alert notifications, "copy shareable
link", rendered-image URLs; ArgoCD's generated links) points at the `ts.net`
hostname and will not resolve for a LAN-only client. Neither supports two base
URLs. The real fix is one hostname reachable from both networks, which needs
split-horizon DNS on the Pi-hole; that is deferred, not solved here.

**ArgoCD's exposure is genuinely wider.** Its tailnet Ingress was tailnet-only by
explicit decision, because ArgoCD holds sync control over every service in the
cluster. It now answers on the LAN as well. This is defensible — it authenticates,
which is exactly the property headlamp lacks — but it is a real change in
boundary, and reverting it is deleting the `extraObjects` block in
`argocd/bootstrap/values.yaml`.

**ArgoCD's LAN Ingress lives in Helm values, not a manifest.** ArgoCD is installed
by `homelab bootstrap`, not by an ApplicationSet, so `argocd/bootstrap/values.yaml`
is the only place its own resources are defined. Changing it means re-running
bootstrap, which is idempotent.

**Grafana's Ingress required turning on its companion Application.**
`kube-prometheus-stack` now sets `extraManifests: "true"`, so `appset-infra-config`
generates a `kube-prometheus-stack-config` Application to sync `manifests/`. The
chart's own `grafana.ingress` block renders only one Ingress and could not carry
a second class.
