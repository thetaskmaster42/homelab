"""Command execution.

Deliberately wraps the system `ssh` binary rather than using paramiko. paramiko
reimplements known_hosts, agent forwarding and ProxyJump — each slightly
differently from OpenSSH — and hides the command it ran. With subprocess, every
command the CLI issues is printable and copy-pasteable, which is what you want at
2am when a node will not join.

Connection pooling is ControlMaster's job, not ours. That is why there is no
pool class here.

Every module takes a Runner by injection, so RecordingRunner makes the whole of
steps/ testable with no network and no cluster.
"""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass, field
from typing import Protocol

from .config import SSH, Node
from .errors import AuthError, RemoteError, Unreachable

# ssh exits 255 for every transport-level failure, so the only way to tell
# "nothing is there" from "it refused my key" is to read what it said. The
# distinction matters: the first is a node that will exist later, the second is
# one that needs a human to install a public key.
_AUTH_MARKERS = (
    "permission denied",
    "too many authentication failures",
    "no supported authentication methods",
    "host key verification failed",
)


@dataclass
class Result:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def out(self) -> str:
        return self.stdout.strip()


class Runner(Protocol):
    host: str

    def run(
        self,
        argv: list[str],
        *,
        check: bool = True,
        sudo: bool = False,
        timeout: int = 300,
        mutates: bool = True,
    ) -> Result: ...


def _last_line(text: str) -> str:
    """ssh prefixes the real reason with known-hosts warnings; the last line is
    almost always the part worth showing."""
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _wrap_sudo(argv: list[str], sudo: bool) -> list[str]:
    # -n: never prompt. A password prompt inside an automation run is a bug, not
    # something to wait on — it hangs the whole install until a timeout.
    return ["sudo", "-n", *argv] if sudo else list(argv)


@dataclass
class SSHRunner:
    """Runs commands on a remote node over OpenSSH."""

    node: Node
    ssh: SSH
    dry_run: bool = False
    log: list[list[str]] = field(default_factory=list)

    @property
    def host(self) -> str:
        return self.node.name

    def _argv(self, argv: list[str]) -> list[str]:
        return [
            "ssh",
            # Never prompt for a password: fail fast instead of hanging.
            "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "ConnectTimeout=10",
            "-o", "ControlMaster=auto",
            "-o", f"ControlPath={self.ssh.controlPath}",
            "-o", "ControlPersist=60s",
            "-i", str(self.ssh.identity_path),
            f"{self.ssh.user}@{self.node.ip}",
            "--",
            # One shell string so pipes and redirects behave as written.
            shlex.join(argv),
        ]

    def run(
        self,
        argv: list[str],
        *,
        check: bool = True,
        sudo: bool = False,
        timeout: int = 300,
        mutates: bool = True,
    ) -> Result:
        argv = _wrap_sudo(argv, sudo)
        full = self._argv(argv)
        self.log.append(argv)

        # Dry run skips only commands that change something. Read-only probes
        # still execute, because a dry run that invents state is worse than no
        # dry run at all — it reports a node as joined when it is unreachable.
        if self.dry_run and mutates:
            return Result(0, "", "")

        try:
            proc = subprocess.run(full, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise Unreachable(self.host, f"timed out after {timeout}s") from exc

        result = Result(proc.returncode, proc.stdout, proc.stderr)
        # 255 is ssh's own transport failure, distinct from the remote command's
        # exit status. Splitting it into "refused my key" and "nothing answered"
        # is what lets `install` skip a node that does not exist yet while still
        # reporting one that is merely misconfigured.
        if result.returncode == 255:
            lowered = result.stderr.lower()
            if any(marker in lowered for marker in _AUTH_MARKERS):
                raise AuthError(self.host, _last_line(result.stderr))
            raise Unreachable(self.host, _last_line(result.stderr))
        if check and not result.ok:
            raise RemoteError(self.host, argv, result.returncode, result.stderr)
        return result


@dataclass
class LocalRunner:
    """Runs commands on this machine — kubectl and helm against the cluster."""

    host: str = "local"
    dry_run: bool = False
    env: dict[str, str] | None = None
    log: list[list[str]] = field(default_factory=list)

    def run(
        self,
        argv: list[str],
        *,
        check: bool = True,
        sudo: bool = False,
        timeout: int = 300,
        mutates: bool = True,
    ) -> Result:
        argv = _wrap_sudo(argv, sudo)
        self.log.append(argv)
        if self.dry_run and mutates:
            return Result(0, "", "")

        import os

        environ = {**os.environ, **(self.env or {})}
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=timeout, env=environ
            )
        except FileNotFoundError as exc:
            raise RemoteError(self.host, argv, 127, f"{argv[0]}: not found") from exc
        except subprocess.TimeoutExpired as exc:
            raise RemoteError(self.host, argv, 124, f"timed out after {timeout}s") from exc

        result = Result(proc.returncode, proc.stdout, proc.stderr)
        if check and not result.ok:
            raise RemoteError(self.host, argv, result.returncode, result.stderr)
        return result


@dataclass
class RecordingRunner:
    """Test double. Records argv, replays canned output, never touches network."""

    host: str = "recorded"
    responses: dict[str, Result] = field(default_factory=dict)
    default: Result = field(default_factory=lambda: Result(0, "", ""))
    log: list[list[str]] = field(default_factory=list)

    def run(
        self,
        argv: list[str],
        *,
        check: bool = True,
        sudo: bool = False,
        timeout: int = 300,
        mutates: bool = True,
    ) -> Result:
        argv = _wrap_sudo(argv, sudo)
        self.log.append(argv)
        joined = shlex.join(argv)
        for needle, response in self.responses.items():
            if needle in joined:
                if check and not response.ok:
                    raise RemoteError(self.host, argv, response.returncode, response.stderr)
                return response
        return self.default

    @property
    def commands(self) -> list[str]:
        return [shlex.join(c) for c in self.log]
