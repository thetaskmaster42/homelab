# Tailscale

Tailscale is how anything here is reached from outside the LAN. There is no port
forwarding, no public IP, and no inbound firewall rule — the operator dials out.

## What the OAuth client needs

Create it at <https://login.tailscale.com/admin/settings/oauth>.

**Do the ACL policy first.** The tag selector in the OAuth form only offers tags
that already exist in your tailnet policy, so creating the client first means
redoing it.

### 1. Tags, in the tailnet policy file

```json
"tagOwners": {
  "tag:k8s-operator": [],
  "tag:k8s": ["tag:k8s-operator"]
}
```

Two tags with a deliberate relationship. `tag:k8s-operator` is the operator's own
device. `tag:k8s` goes on every proxy device the operator creates — one per
exposed Service — and `tag:k8s-operator` **owns** it, which is what authorises
the operator to register those devices on your behalf.

`tag:k8s-operator` has an empty owner list: nobody may apply it manually, only
the OAuth client that holds it.

### 2. Scopes

Three, all **write**, each granted with the tag `tag:k8s-operator`:

| Scope | Why |
|---|---|
| **General / Services** | registers the tailnet services backing each Ingress |
| **Devices / Core** | creates and deletes the proxy devices |
| **Keys / Auth Keys** | mints the auth keys those devices register with |

Read scopes are not required. When you expand a Write scope a **Tags** field
appears — select `tag:k8s-operator` there. A client with the right scopes but no
tag fails at device registration, and the error does not mention tags, which
makes it a slow thing to diagnose.

### 3. Store the credentials

The client ID and secret go into `clusters/rps/bootstrap-secrets.enc.yaml`,
encrypted — see [secrets.md](secrets.md). `homelab bootstrap` applies them as
`operator-oauth` in the `tailscale` namespace.

## Accessing the Kubernetes API over the tailnet

`apiServerProxyConfig.mode` is on, so the API server is reachable at the
operator's tailnet name with no VPN or port forward.

`allowImpersonation` is also on, and that is the part that matters for safety.
Without it the proxy forwards every request using the *operator's* identity, so
anyone who can reach it inherits the operator's permissions. With it, the proxy
sets Kubernetes impersonation headers from the caller's Tailscale identity, and
access is decided by a tailnet grant plus ordinary Kubernetes RBAC.

That requires a grant. Until one exists, nobody can reach the API — which is the
correct direction to fail in.

```json
"grants": [
  {
    "src": ["autogroup:owner"],
    "dst": ["tag:k8s-operator"],
    "app": {
      "tailscale.com/cap/kubernetes": [
        {"impersonate": {"groups": ["system:masters"]}}
      ]
    }
  }
]
```

`system:masters` is cluster-admin. Reasonable for a single-operator homelab,
but it is a real grant: narrow `src`, or map to a group bound to a read-only
ClusterRole, if anyone else ever joins the tailnet.

## Exposing an application

```yaml
ingressClassName: tailscale
```

gives a private tailnet hostname with a real Let's Encrypt certificate, reachable
from your devices anywhere and nobody else's.

Adding `tailscale.com/funnel: "true"` makes it **genuinely public on the
internet**. Before setting it, be explicit about what authentication the app has
— `prep-tracker` deliberately stays on the tailnet because it has none, and
`headlamp` must never be funneled because it holds a cluster-admin binding.

Funnel additionally requires HTTPS and MagicDNS enabled in the tailnet, and a
Funnel-permitting node attribute in the policy file. All three are one-time
settings that are easy to forget.
