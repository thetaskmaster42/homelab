"""Load and validate clusters/<name>/cluster.yaml.

Validation here is not ceremony. Every rule below corresponds to a failure that
is expensive to diagnose once it has reached a Raspberry Pi over SSH — a second
server silently forming a split cluster, a typo'd IP joining an agent to
nothing, a pod CIDR that overlaps the LAN and blackholes the node you are
sitting on.
"""

from __future__ import annotations

import ipaddress
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .errors import ConfigError

K3S_VERSION = re.compile(r"^v\d+\.\d+\.\d+\+k3s\d+$")


class Strict(BaseModel):
    # An unknown key is nearly always a typo, and silently ignoring it means the
    # setting the user thought they changed never took effect.
    model_config = ConfigDict(extra="forbid")


class Node(Strict):
    name: str
    ip: ipaddress.IPv4Address
    role: Literal["server", "agent"]
    smallDisk: bool = False

    @property
    def is_server(self) -> bool:
        return self.role == "server"


class SSH(Strict):
    user: str
    identityFile: str
    controlPath: str = "~/.ssh/cm-%r@%h:%p"

    @property
    def identity_path(self) -> Path:
        return Path(self.identityFile).expanduser()


class K3s(Strict):
    version: str
    clusterCIDR: ipaddress.IPv4Network
    serviceCIDR: ipaddress.IPv4Network
    serverArgs: list[str] = Field(default_factory=list)

    @field_validator("version")
    @classmethod
    def _pinned(cls, v: str) -> str:
        if not K3S_VERSION.match(v):
            raise ValueError(
                f"k3s version {v!r} must be fully pinned, e.g. v1.36.3+k3s1. "
                f"An unpinned version means two nodes installed a week apart "
                f"silently run different releases."
            )
        return v


class CNI(Strict):
    # calico  — installed by the CLI via the tigera operator, before ArgoCD exists.
    # flannel — bundled with k3s and started by it; the CLI installs nothing.
    # none    — nothing schedules until you install a CNI yourself.
    provider: Literal["calico", "flannel", "none"]

    # Calico only. Flannel has no version of its own: it ships inside the k3s
    # binary, so its version is k3s's version.
    version: str = ""
    # Calico only.
    encapsulation: Literal["VXLAN", "IPIP", "None"] = "VXLAN"

    # Flannel only, and it must agree with --flannel-backend in k3s.serverArgs:
    # this field documents the choice, that flag enacts it. host-gw skips
    # encapsulation entirely and is measurably cheaper, but requires every node
    # on one L2 segment — true here, and the thing to re-check before adding a
    # node somewhere else.
    backend: Literal["vxlan", "host-gw", "wireguard-native"] = "vxlan"

    @model_validator(mode="after")
    def _calico_needs_a_version(self) -> "CNI":
        if self.provider == "calico" and not self.version:
            raise ValueError(
                "cni.version is required when cni.provider is 'calico' — the "
                "tigera-operator manifest URL is built from it."
            )
        return self


class ArgoCD(Strict):
    chartVersion: str
    valuesFile: str
    repoURL: str
    revision: str
    rootApp: str


class Network(Strict):
    loadBalancerPool: str
    ingressIP: ipaddress.IPv4Address


class Spec(Strict):
    ssh: SSH
    k3s: K3s
    cni: CNI
    argocd: ArgoCD
    network: Network
    nodes: list[Node]


class Metadata(Strict):
    name: str


class Cluster(Strict):
    apiVersion: str
    kind: Literal["Cluster"]
    metadata: Metadata
    spec: Spec

    # --- convenience accessors, so callers never walk .spec.* by hand ---

    @property
    def name(self) -> str:
        return self.metadata.name

    @property
    def nodes(self) -> list[Node]:
        return self.spec.nodes

    @property
    def server(self) -> Node:
        return next(n for n in self.spec.nodes if n.is_server)

    @property
    def agents(self) -> list[Node]:
        return [n for n in self.spec.nodes if not n.is_server]

    def node(self, name: str) -> Node:
        for n in self.spec.nodes:
            if n.name == name:
                return n
        known = ", ".join(n.name for n in self.spec.nodes)
        raise ConfigError(f"no node named {name!r}. Known nodes: {known}")

    # --- cross-field rules ---

    @model_validator(mode="after")
    def _exactly_one_server(self) -> "Cluster":
        servers = [n for n in self.spec.nodes if n.is_server]
        if len(servers) != 1:
            names = ", ".join(n.name for n in servers) or "none"
            raise ValueError(
                f"exactly one node must have role: server, found {len(servers)} ({names}). "
                f"Multi-server HA needs --cluster-init and an embedded etcd datastore, "
                f"which this CLI does not yet set up — two plain servers would form two "
                f"separate clusters."
            )
        return self

    @model_validator(mode="after")
    def _unique_names_and_ips(self) -> "Cluster":
        for label, values in (
            ("name", [n.name for n in self.spec.nodes]),
            ("ip", [str(n.ip) for n in self.spec.nodes]),
        ):
            dupes = {v for v in values if values.count(v) > 1}
            if dupes:
                raise ValueError(f"duplicate node {label}(s): {sorted(dupes)}")
        return self

    @model_validator(mode="after")
    def _cidrs_do_not_overlap_nodes(self) -> "Cluster":
        """A pod or service CIDR that contains a node IP blackholes that node the
        moment the CNI comes up — including, potentially, the machine you are
        running this from."""
        for label, cidr in (
            ("clusterCIDR", self.spec.k3s.clusterCIDR),
            ("serviceCIDR", self.spec.k3s.serviceCIDR),
        ):
            for node in self.spec.nodes:
                if node.ip in cidr:
                    raise ValueError(
                        f"{label} {cidr} contains node {node.name} ({node.ip}). "
                        f"Routing for that node would be captured by the CNI."
                    )
        if self.spec.k3s.clusterCIDR.overlaps(self.spec.k3s.serviceCIDR):
            raise ValueError(
                f"clusterCIDR {self.spec.k3s.clusterCIDR} overlaps serviceCIDR "
                f"{self.spec.k3s.serviceCIDR}"
            )
        return self

    @model_validator(mode="after")
    def _ingress_ip_inside_lb_pool(self) -> "Cluster":
        """Traefik pins network.ingressIP. If it sits outside the MetalLB pool the
        Service stays <pending> forever with no obvious explanation."""
        pool = self.spec.network.loadBalancerPool
        if "-" not in pool:
            raise ValueError(f"loadBalancerPool {pool!r} must be a start-end range")
        start, end = (ipaddress.IPv4Address(p.strip()) for p in pool.split("-", 1))
        if start > end:
            raise ValueError(f"loadBalancerPool {pool!r} start is after its end")
        if not (start <= self.spec.network.ingressIP <= end):
            raise ValueError(
                f"network.ingressIP {self.spec.network.ingressIP} is outside "
                f"loadBalancerPool {pool}. MetalLB would never assign it."
            )
        return self


def load(path: Path) -> Cluster:
    if not path.is_file():
        raise ConfigError(f"no cluster config at {path}")
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} does not contain a mapping")
    try:
        return Cluster.model_validate(raw)
    except Exception as exc:
        raise ConfigError(f"{path} is not a valid cluster config:\n{exc}") from exc


def find(repo_root: Path, name: str) -> Cluster:
    return load(repo_root / "clusters" / name / "cluster.yaml")
