# 0011 — Flannel instead of Calico

**Status:** accepted, 2026-08-29
**Reverses:** the CNI choice implicit since the v3 rebuild, where k3s ran with
`--flannel-backend=none` so the CLI could install Calico.

## Context

### What prompted this

Two nodes went unreachable in as many days. `k3s-worker-1` on 2026-08-27 and
`k3s-server` on 2026-08-28, both with the same signature: the node alive and its
kernel logging normally, but answering no ARP at all, so every other host on the
subnet saw only `FAILED`/`INCOMPLETE`. `k3s-server` stayed that way for roughly
six hours and `k3s-worker-1` for two, each ending in a manual power cycle. Power
was ruled out on both (5000 mA negotiated, `in0_lcrit_alarm = 0`), as were
panics, OOM, thermal events and disk errors.

Calico became a suspect for the ordinary reason: it owned by far the largest
share of dataplane state on those nodes — 163 iptables chains, over 1200 rules
in the filter table alone, its own VXLAN device and route table.

**Causation was never established, and this ADR should not be read as claiming
it.** The observed failure was at layer 2, below anything a CNI touches: the
node stopped answering ARP for its own `eth0`, which is the kernel's business
rather than Calico's. The honest position is that the drops made the CNI worth
re-examining, and that re-examination found the case below independently strong.

That leaves a genuinely useful test. **If the drops recur under flannel, Calico
is exonerated** and the search moves to the Pi 5 `macb`/RP1 driver, the cabling,
or the switch ports. If they stop, that is suggestive but still not proof — two
nodes over two days is not a controlled experiment. Either way the evidence is
cheaper to gather now: `scripts/collect-crash-evidence.sh` captures the
post-mortem, and `scripts/net-watchdog.sh` captures the interface counters that
a reboot destroys.

### The case that stands on its own

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

`steps/cni.py` is now only the node-readiness wait, and the config model no
longer accepts `calico` as a provider. The tigera-operator install is in the git
history if it is ever wanted back; keeping a dead code path that nothing
exercises would have been the worse of the two options.

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
- **`homelab nuke` now tears down the CNI dataplane too.** Neither
  `k3s-uninstall.sh` nor its agent counterpart touches a CNI they did not
  install, and the first flannel rebuild proved it: `vxlan.calico` still up on
  all three nodes, stale routes including IPAM blackholes, and 1249/292/210
  iptables rules still loaded. Nothing persisted them, so the nodes were
  rebooted once to clear the backlog — but a teardown that needs a reboot to
  finish is not a teardown, so the cleanup is now part of `nuke`.
- **`calico-apiserver` currently contributes a seventh NetworkPolicy** which
  disappears with it. Only the six ArgoCD ones need to survive the move.
