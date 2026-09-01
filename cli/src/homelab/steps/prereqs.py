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
    ("netsnap-sentinel.sh", "/usr/local/bin/netsnap-sentinel.sh", "0755"),
    ("netsnap-preboot.sh", "/usr/local/bin/netsnap-preboot.sh", "0755"),
    ("netsnap-archive.sh", "/usr/local/bin/netsnap-archive.sh", "0755"),
    ("netsnap-sentinel.service", "/etc/systemd/system/netsnap-sentinel.service", "0644"),
    ("netsnap-preboot.service", "/etc/systemd/system/netsnap-preboot.service", "0644"),
    ("netsnap-archive.service", "/etc/systemd/system/netsnap-archive.service", "0644"),
    ("netsnap-archive.timer", "/etc/systemd/system/netsnap-archive.timer", "0644"),
    # udev, not a systemd unit. The unit this replaces was bound to
    # sys-subsystem-net-devices-eth0.device and silently failed to run on a fresh
    # boot -- enabled, symlinked, device active, service never started. udev
    # fires on the device event itself.
    ("99-disable-eee.rules", "/etc/udev/rules.d/99-disable-eee.rules", "0644"),
)

NODE_UNITS = ("netsnap-preboot.service", "netsnap-sentinel.service", "netsnap-archive.timer")

# Enabled, but never restarted by `install`. This unit rotates the previous
# boot's snapshot into preboot-1 and preboot-1 into preboot-2, which is exactly
# right once per boot and destructive every other time: re-running it would push
# real pre-outage evidence out of the two-deep history and replace it with a
# snapshot taken from a healthy node. It runs at boot or not at all.
BOOT_ONLY_UNITS = frozenset({"netsnap-preboot.service"})

# Removed rather than left running. The once-a-minute rotation destroyed the only
# snapshot that ever mattered -- two files at that cadence gave a two-minute
# window, and a recovery reboot consumed it before anyone could read it. The
# device-bound EEE unit is superseded by the udev rule.
SUPERSEDED_UNITS = ("netsnap.timer", "netsnap.service", "eee-off@eth0.service")
SUPERSEDED_FILES = (
    "/etc/systemd/system/netsnap.timer",
    "/etc/systemd/system/netsnap.service",
    "/etc/systemd/system/eee-off@.service",
    "/usr/local/bin/netsnap-rotate.sh",
)


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


def remove_superseded(runner: Runner) -> None:
    """Stop and delete units this version replaces.

    Without this a re-run leaves the old once-a-minute timer running alongside
    the new sentinel, both writing to the same directory -- and the old one
    rotates away exactly the files the new one exists to preserve.
    """
    for unit in SUPERSEDED_UNITS:
        runner.run(["systemctl", "disable", "--now", unit], sudo=True, timeout=60, check=False)
    runner.run(
        ["bash", "-c", "rm -f " + " ".join(SUPERSEDED_FILES)],
        sudo=True, timeout=60, check=False,
    )


def enable_node_units(runner: Runner, probe_ips: str | None = None) -> None:
    """Reload and enable. `enable --now` is idempotent, which is what makes
    re-running `homelab install` on a live node safe."""
    if probe_ips:
        # A drop-in rather than editing the unit: the shipped file stays byte
        # identical to the repo, and the per-node value is visibly separate.
        drop_in = (
            "# Rendered by `homelab install` from cluster.yaml, so the peer list\n"
            "# cannot drift from the node inventory.\n"
            "[Service]\n"
            # QUOTED. systemd splits Environment= on whitespace, so an unquoted
            # multi-value assignment silently drops everything after the first
            # space -- each node would probe one peer, and losing that single
            # peer would look like isolation and eventually trigger a reboot.
            f'Environment="PROBE_IPS={probe_ips}"\n'
        )
        runner.run(
            ["bash", "-c",
             "mkdir -p /etc/systemd/system/netsnap-sentinel.service.d && "
             + write_file_script(drop_in, "/etc/systemd/system/netsnap-sentinel.service.d/probe-ips.conf", "0644")],
            sudo=True, timeout=60,
        )
    runner.run(["systemctl", "daemon-reload"], sudo=True, timeout=60)
    runner.run(["udevadm", "control", "--reload-rules"], sudo=True, timeout=60, check=False)
    for unit in NODE_UNITS:
        runner.run(["systemctl", "enable", "--now", unit], sudo=True, timeout=60, check=False)
        # `enable --now` starts a stopped unit but leaves a running one alone, so
        # an updated script or drop-in would sit on disk unused until the next
        # reboot -- and `install` would report success while the fix was inert.
        # Restart to make the deployed content actually the running content.
        if unit not in BOOT_ONLY_UNITS:
            runner.run(["systemctl", "restart", unit], sudo=True, timeout=60, check=False)


def ensure(
    runner: Runner,
    source_dir: Path | None = None,
    probe_ips: str | None = None,
) -> list[str]:
    """Install anything missing. Returns what was installed, for reporting."""
    missing = missing_packages(runner)
    install_packages(runner, missing)
    if source_dir is not None:
        remove_superseded(runner)
        install_node_files(runner, source_dir)
        enable_node_units(runner, probe_ips=probe_ips)
    return missing
