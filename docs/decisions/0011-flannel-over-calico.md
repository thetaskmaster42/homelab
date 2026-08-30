# 0011 — Flannel instead of Calico

**Status:** accepted, 2026-08-29
**Reverses:** the CNI choice implicit since the v3 rebuild, where k3s ran with
`--flannel-backend=none` so the CLI could install Calico.

## Context

Calico was chosen for a policy engine this cluster never used. In the whole
repository there is not one `NetworkPolicy`, no `GlobalNetworkPolicy`, no host
endpoint, and the eBPF dataplane was never enabled. What Calico actually did
here was move packets — the job flannel does, bundled inside the k3s binary.

Measured against the running cluster, that cost:

| Namespace | Pods | Memory |
|---|---|---|
| `calico-system` (node ×3, typha ×2, csi-node-driver ×3, kube-controllers) | 9 | 805 MiB |
| `calico-apiserver` | 2 | 141 MiB |
| `tigera-operator` | 1 | 87 MiB |
| **Total** | **12** | **~1 GiB**, ~100m CPU |

`calico-node` alone was 146–219 MiB *per node*. On three 16 GiB Pis that is a
real fraction of the budget, spent on capability that was not in use.

## Decision

Run k3s's bundled flannel. The CLI installs no CNI at all.

```
--flannel-backend=vxlan       (was: --flannel-backend=none)
                              (removed: --disable-network-policy)
```

`vxlan` rather than `host-gw`, deliberately: Calico was already running VXLAN, so
this changes the CNI without also changing the encapsulation. `host-gw` skips
encapsulation entirely and is measurably cheaper, and every node here is on one
L2 segment so it would work — that is a later, separate change, and the
precondition to re-check before adding a node on another segment.

### The flag whose removal matters most

`--disable-network-policy` turned off k3s's kube-router policy controller,
because Calico enforced `NetworkPolicy` instead. **Dropping Calico while leaving
that flag set would have been a silent security regression.**

The ArgoCD chart ships six NetworkPolicies, and they are enforced today:

```
argocd-application-controller   argocd-notifications-controller   argocd-repo-server
argocd-dex-server               argocd-redis                      argocd-server
```

With Calico gone and kube-router still disabled, those objects would remain in
the API, `kubectl get networkpolicy` would still list them, and nothing would
enforce them. No error, no event, no warning — a policy that fails open and
looks fine. Both flags had to go together, and that is the whole reason this ADR
exists rather than a one-line commit.

## The chicken-and-egg dissolves

Previous documentation said the CNI "can never be GitOps-managed" because no pod
schedules without a CNI and ArgoCD is a pod. That was true of Calico, which is a
separate install the CLI had to perform between k3s and ArgoCD.

It is not true of flannel, which is *inside* the k3s binary and running before
the API server serves its first request. The constraint has not been worked
around; it no longer applies. `cni.install()` returns immediately for flannel.

The Calico path is kept in `steps/cni.py` and in the config model, so reversing
this is a `cluster.yaml` change rather than a rewrite.

## What is given up

- **Calico's policy extensions** — `GlobalNetworkPolicy`, host endpoint
  protection, DNS policy, tiered policy. None were used. Plain namespaced
  `NetworkPolicy` still works, enforced by kube-router.
- **Calico's observability** — Felix metrics, `calicoctl`, the calico-apiserver.
  Nothing consumed them; no dashboard or alert referenced Calico.
- **eBPF dataplane and WireGuard encryption**, neither enabled.
- **Policy enforcement changes implementation.** kube-router is a different
  engine from Felix. For plain ingress/egress rules the semantics are the
  Kubernetes ones and should be equivalent, but it is a different implementation
  and the ArgoCD policies are worth spot-checking after the rebuild.

## Consequences

- **~1 GiB of RAM and 12 pods returned** across three nodes.
- **One fewer thing between k3s and ArgoCD.** `homelab install` no longer applies
  the tigera operator, no longer retries the `Installation` CR waiting for CRDs
  to register, and no longer has a failure mode there.
- **Applies only on rebuild.** The CNI is fixed at k3s install time; this needs
  `homelab nuke && homelab install`, not a push.
- **`calico-apiserver` currently contributes a seventh NetworkPolicy** which
  disappears with it. Only the six ArgoCD ones need to survive the move.
