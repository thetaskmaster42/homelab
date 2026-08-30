"""Node readiness after the CNI is up.

There is no CNI install step. Flannel ships inside the k3s binary and is started
by k3s itself from `--flannel-backend`, before the API server serves its first
request — so by the time anything here could run, the network already works.

That is a change from what this module used to be. Calico was a separate install
the CLI had to perform between k3s and ArgoCD, and it was the reason this repo
documented a permanent chicken-and-egg: no pod schedules without a CNI, ArgoCD is
a pod, therefore the CNI cannot be GitOps-managed. With flannel the constraint
does not arise. See docs/decisions/0011-flannel-over-calico.md.

What remains is the wait. k3s reports a node NotReady until its CNI has posted a
network config, so this is still the honest gate between "agents joined" and
"anything can be scheduled".
"""

from __future__ import annotations

from ..runner import Runner


def wait_for_nodes_ready(runner: Runner, timeout: int = 300) -> None:
    runner.run(
        ["kubectl", "wait", "--for=condition=Ready", "node", "--all", f"--timeout={timeout}s"],
        timeout=timeout + 60,
    )
