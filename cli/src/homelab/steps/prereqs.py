"""OS packages the cluster needs before workloads can use it.

This exists because of a real failure: the nfs-provisioner service synced
cleanly and its pod then sat in ContainerCreating forever with
`mount failed: exit status 32`. The cause was that no node had `nfs-common`, so
there was no /sbin/mount.nfs helper. ArgoCD cannot fix that — mounting happens
in the kubelet, below Kubernetes entirely — so it belongs to the CLI, on the
same side of the boundary as k3s and the CNI.

It also installs the node-level units in scripts/node/. Same argument: they
configure the NIC and read kernel state, which is below Kubernetes entirely, so
ArgoCD cannot own them. Putting them here is what makes them survive a rebuild —
a diagnostic that only exists on the node you happened to copy it to is not a
diagnostic.

Kept deliberately narrow: only packages without which a declared infra service
cannot function. This is not a configuration-management system, and the moment
it starts growing application dependencies it has become one.
"""

from __future__ import annotations

from pathlib import Path

from ..runner import Runner

# nfs-common: /sbin/mount.nfs, required by infra/services/nfs-provisioner.
# ethtool:    required by the eee-off unit and by netsnap.sh; present on the
#             current images, but assuming that is how a rebuild fails.
REQUIRED_PACKAGES = ("nfs-common", "ethtool")

# (source in scripts/node/, destination on the node, mode)
NODE_FILES = (
    ("netsnap.sh", "/usr/local/bin/netsnap.sh", "0755"),
    ("netsnap-rotate.sh", "/usr/local/bin/netsnap-rotate.sh", "0755"),
    ("netsnap.service", "/etc/systemd/system/netsnap.service", "0644"),
    ("netsnap.timer", "/etc/systemd/system/netsnap.timer", "0644"),
    ("eee-off@.service", "/etc/systemd/system/eee-off@.service", "0644"),
)

# Enabled by instance name, so the interface is named in exactly one place.
NODE_UNITS = ("netsnap.timer", "eee-off@eth0.service")


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


def write_file_script(content: str, dest: str, mode: str) -> str:
    """A command that writes `content` to `dest` atomically.

    Written to a temp file and moved into place rather than redirected straight
    at the destination: a truncated systemd unit or a half-written script is
    worse than the old version, and these are being installed on nodes that
    have been dropping off the network mid-operation.

    The heredoc delimiter is quoted, so nothing in the payload is expanded by
    the shell on the way in.
    """
    return (
        f"set -e; tmp=$(mktemp); cat > \"$tmp\" <<'HOMELAB_NODE_EOF'\n"
        f"{content}\n"
        f"HOMELAB_NODE_EOF\n"
        f"install -D -m {mode} \"$tmp\" {dest}; rm -f \"$tmp\""
    )


def install_node_files(runner: Runner, source_dir: Path) -> list[str]:
    """Push scripts/node/* onto the node. Returns the destinations written."""
    written = []
    for name, dest, mode in NODE_FILES:
        content = (source_dir / name).read_text()
        runner.run(
            ["bash", "-c", write_file_script(content, dest, mode)],
            sudo=True,
            timeout=60,
        )
        written.append(dest)
    return written


def enable_node_units(runner: Runner) -> None:
    """Reload and enable. `enable --now` is idempotent, which is what makes
    re-running `homelab install` on a live node safe."""
    runner.run(["systemctl", "daemon-reload"], sudo=True, timeout=60)
    for unit in NODE_UNITS:
        runner.run(["systemctl", "enable", "--now", unit], sudo=True, timeout=60, check=False)


def ensure(runner: Runner, source_dir: Path | None = None) -> list[str]:
    """Install anything missing. Returns what was installed, for reporting."""
    missing = missing_packages(runner)
    install_packages(runner, missing)
    if source_dir is not None:
        install_node_files(runner, source_dir)
        enable_node_units(runner)
    return missing
