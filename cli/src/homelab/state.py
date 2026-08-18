"""Local record of which nodes have been touched.

Not git-backed, and that is a correction to an earlier design. The join token is
a cluster-admin credential and this repository is public; timestamps churn on
every run and would pollute history; and the real state is the cluster, which is
authoritative. This file is a cache used for idempotency and for `nuke` to know
what it is tearing down.

The token itself is never written — only a fingerprint, which is enough to detect
"the server was rebuilt, so the agents must rejoin".
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

SCHEMA_VERSION = 1


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def fingerprint(token: str) -> str:
    """A fingerprint, never the token. Enough to notice the server changed."""
    return "sha256:" + hashlib.sha256(token.encode()).hexdigest()[:16]


@dataclass
class NodeState:
    ip: str = ""
    role: str = ""
    phase: str = "unknown"  # unknown | installed | joined | pending | removed
    lastSeen: str = ""
    note: str = ""


@dataclass
class State:
    cluster: str
    schemaVersion: int = SCHEMA_VERSION
    updatedAt: str = ""
    k3sVersion: str = ""
    tokenFingerprint: str = ""
    argocdInstalled: bool = False
    nodes: dict[str, NodeState] = field(default_factory=dict)

    def mark(self, name: str, *, phase: str, ip: str = "", role: str = "", note: str = "") -> None:
        entry = self.nodes.setdefault(name, NodeState())
        entry.phase = phase
        entry.lastSeen = _now()
        if ip:
            entry.ip = ip
        if role:
            entry.role = role
        entry.note = note

    def phase(self, name: str) -> str:
        entry = self.nodes.get(name)
        return entry.phase if entry else "unknown"

    @property
    def pending(self) -> list[str]:
        """Nodes in cluster.yaml that have not joined yet — typically hardware that
        does not exist at install time and gets added afterwards."""
        return sorted(n for n, s in self.nodes.items() if s.phase == "pending")


def path_for(repo_root: Path, cluster: str) -> Path:
    return repo_root / ".homelab" / "state" / f"{cluster}.yaml"


def load(repo_root: Path, cluster: str) -> State:
    p = path_for(repo_root, cluster)
    if not p.is_file():
        return State(cluster=cluster)
    raw = yaml.safe_load(p.read_text()) or {}
    nodes = {k: NodeState(**v) for k, v in (raw.pop("nodes", {}) or {}).items()}
    raw.pop("schemaVersion", None)
    known = {"cluster", "updatedAt", "k3sVersion", "tokenFingerprint", "argocdInstalled"}
    return State(nodes=nodes, **{k: v for k, v in raw.items() if k in known})


def save(repo_root: Path, state: State) -> Path:
    p = path_for(repo_root, state.cluster)
    p.parent.mkdir(parents=True, exist_ok=True)
    state.updatedAt = _now()

    payload = {
        "cluster": state.cluster,
        "schemaVersion": SCHEMA_VERSION,
        "updatedAt": state.updatedAt,
        "k3sVersion": state.k3sVersion,
        "tokenFingerprint": state.tokenFingerprint,
        "argocdInstalled": state.argocdInstalled,
        "nodes": {k: asdict(v) for k, v in sorted(state.nodes.items())},
    }

    # Atomic replace, so an interrupted write cannot leave a truncated state file
    # that then fails to parse on the next run.
    tmp = p.with_suffix(".tmp")
    tmp.write_text(yaml.safe_dump(payload, sort_keys=False))
    if p.exists():
        p.replace(p.with_suffix(".bak"))
    os.replace(tmp, p)
    return p
