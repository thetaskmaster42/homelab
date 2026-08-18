"""Checks that run before anything is changed.

Every check here exists because its absence produces a failure that is confusing
rather than obvious. Clock skew does not say "clock skew", it says the TLS
handshake failed. A busy port 6443 does not say "something else is running", it
says the node never became Ready.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import Cluster, Node
from ..errors import AuthError, Unreachable
from ..runner import Runner

EXPECTED_ARCH = "aarch64"
MAX_CLOCK_SKEW_SECONDS = 5
MIN_FREE_GB = 8


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    # True when the node answered but is misconfigured, so retrying later cannot
    # help. Distinguishes "install the key" from "this hardware does not exist yet".
    fatal: bool = False

    @property
    def symbol(self) -> str:
        return "ok  " if self.ok else "FAIL"


def check_node(runner: Runner, node: Node, *, local_epoch: int | None = None) -> list[Check]:
    checks: list[Check] = []

    try:
        whoami = runner.run(["id", "-un"], check=False, timeout=20)
    except AuthError as exc:
        # The host is up and listening — waiting will not fix this.
        return [Check("ssh", False, f"key rejected — {exc.detail}", fatal=True)]
    except Unreachable as exc:
        return [Check("ssh", False, exc.detail or "no response")]

    if not whoami.ok:
        return [Check("ssh", False, whoami.stderr.strip()[:120])]
    checks.append(Check("ssh", True, f"as {whoami.out}"))

    sudo = runner.run(["true"], check=False, sudo=True, timeout=20)
    checks.append(
        Check(
            "sudo -n",
            sudo.ok,
            "" if sudo.ok else "passwordless sudo is required; automation cannot answer a prompt",
        )
    )

    arch = runner.run(["uname", "-m"], check=False, timeout=20)
    checks.append(
        Check(
            "arch",
            arch.ok and arch.out == EXPECTED_ARCH,
            arch.out or arch.stderr.strip()[:80],
        )
    )

    hostname = runner.run(["hostname", "-s"], check=False, timeout=20)
    if hostname.ok:
        checks.append(
            Check(
                "hostname",
                hostname.out == node.name,
                f"{hostname.out} (cluster.yaml says {node.name})"
                if hostname.out != node.name
                else node.name,
            )
        )

    # Free space on the k3s data path's filesystem, in GB.
    disk = runner.run(
        ["bash", "-c", "df -BG --output=avail / | tail -1 | tr -dc '0-9'"],
        check=False,
        timeout=20,
    )
    if disk.ok and disk.out.isdigit():
        free = int(disk.out)
        checks.append(
            Check("disk", free >= MIN_FREE_GB, f"{free}Gi free (need {MIN_FREE_GB}Gi)")
        )

    if local_epoch is not None:
        remote = runner.run(["date", "+%s"], check=False, timeout=20)
        if remote.ok and remote.out.isdigit():
            skew = abs(int(remote.out) - local_epoch)
            checks.append(
                Check(
                    "clock",
                    skew <= MAX_CLOCK_SKEW_SECONDS,
                    f"{skew}s skew — TLS validation fails in confusing ways beyond "
                    f"{MAX_CLOCK_SKEW_SECONDS}s"
                    if skew > MAX_CLOCK_SKEW_SECONDS
                    else f"{skew}s skew",
                )
            )

    return checks


def check_local(cluster: Cluster, tools: tuple[str, ...] = ("kubectl", "helm")) -> list[Check]:
    import shutil

    checks = [
        Check(
            f"local {tool}",
            shutil.which(tool) is not None,
            "" if shutil.which(tool) else "not on PATH",
        )
        for tool in tools
    ]
    key = cluster.spec.ssh.identity_path
    checks.append(Check("ssh key", key.is_file(), str(key)))
    return checks
