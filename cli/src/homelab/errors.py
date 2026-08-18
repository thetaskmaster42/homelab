"""Exceptions the CLI turns into clean messages rather than tracebacks."""

from __future__ import annotations


class HomelabError(Exception):
    """Anything the user can act on. cli.main prints these without a traceback."""


class ConfigError(HomelabError):
    pass


class PreflightError(HomelabError):
    pass


class RemoteError(HomelabError):
    def __init__(self, host: str, argv: list[str], returncode: int, stderr: str):
        self.host = host
        self.argv = argv
        self.returncode = returncode
        self.stderr = stderr.strip()
        super().__init__(
            f"[{host}] command failed (exit {returncode}): {' '.join(argv)}\n{self.stderr}"
        )


class Unreachable(HomelabError):
    """Nothing answered at that address.

    Distinct from RemoteError because a node that has not been built yet is an
    expected state, not a failure — `install` records it as pending and carries
    on, and a later run joins it.
    """

    def __init__(self, host: str, detail: str = ""):
        self.host = host
        self.detail = detail.strip()
        super().__init__(f"[{host}] unreachable{': ' + self.detail if self.detail else ''}")


class AuthError(HomelabError):
    """The host answered but refused the key.

    Deliberately NOT an Unreachable: the machine exists and is listening, so
    waiting will not help. Someone has to install the public key. Conflating the
    two would report a misconfigured node as "not built yet" and quietly skip it
    forever.
    """

    def __init__(self, host: str, detail: str = ""):
        self.host = host
        self.detail = detail.strip()
        super().__init__(f"[{host}] authentication failed: {self.detail}")
