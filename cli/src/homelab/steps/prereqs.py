"""OS packages the cluster needs before workloads can use it.

This exists because of a real failure: the nfs-provisioner service synced
cleanly and its pod then sat in ContainerCreating forever with
`mount failed: exit status 32`. The cause was that no node had `nfs-common`, so
there was no /sbin/mount.nfs helper. ArgoCD cannot fix that — mounting happens
in the kubelet, below Kubernetes entirely — so it belongs to the CLI, on the
same side of the boundary as k3s and the CNI.

Kept deliberately narrow: only packages without which a declared infra service
cannot function. This is not a configuration-management system, and the moment
it starts growing application dependencies it has become one.
"""

from __future__ import annotations

from ..runner import Runner

# nfs-common: provides /sbin/mount.nfs, required by infra/services/nfs-provisioner.
REQUIRED_PACKAGES = ("nfs-common",)


def missing_packages(runner: Runner, packages: tuple[str, ...] = REQUIRED_PACKAGES) -> list[str]:
    missing = []
    for pkg in packages:
        installed = runner.run(
            ["dpkg-query", "-W", "-f=${Status}", pkg], check=False, mutates=False
        )
        if not installed.ok or "install ok installed" not in installed.stdout:
            missing.append(pkg)
    return missing


def install_packages(runner: Runner, packages: list[str]) -> None:
    if not packages:
        return
    # Update and install in one shell so the index refresh cannot be skipped by a
    # cached layer, and so a stale index does not cause a spurious 404.
    runner.run(
        [
            "bash", "-c",
            "DEBIAN_FRONTEND=noninteractive apt-get update -qq && "
            "DEBIAN_FRONTEND=noninteractive apt-get install -y -qq " + " ".join(packages),
        ],
        sudo=True,
        timeout=600,
    )


def ensure(runner: Runner) -> list[str]:
    """Install anything missing. Returns what was installed, for reporting."""
    missing = missing_packages(runner)
    install_packages(runner, missing)
    return missing
