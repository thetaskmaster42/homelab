"""k3s server install, agent join, and teardown.

The command *builders* here are pure functions returning strings. That is the
whole testability strategy: `tests/test_k3s_commands.py` asserts the exact script
produced for a given cluster.yaml against golden files, so dropping
`--disable=servicelb` fails on a laptop in milliseconds instead of on three
Raspberry Pis ten minutes into a rebuild.

Nothing in this module executes anything except the `install_*` / `uninstall_*`
functions at the bottom, which take a Runner.
"""

from __future__ import annotations

import shlex

from ..config import Cluster, Node
from ..errors import HomelabError
from ..runner import Runner

K3S_INSTALL_URL = "https://get.k3s.io"
SERVER_TOKEN_PATH = "/var/lib/rancher/k3s/server/node-token"
KUBECONFIG_PATH = "/etc/rancher/k3s/k3s.yaml"


# --------------------------------------------------------------------------
# Pure command builders
# --------------------------------------------------------------------------

def server_exec_args(cluster: Cluster) -> list[str]:
    """The INSTALL_K3S_EXEC argument list for the control plane.

    serverArgs comes from cluster.yaml so the disables are reviewable in git
    rather than buried in Python. The CIDRs and the node-specific flags are
    derived, because deriving them is exactly what stops them drifting from the
    node inventory.
    """
    server = cluster.server
    args = [
        "server",
        *cluster.spec.k3s.serverArgs,
        f"--cluster-cidr={cluster.spec.k3s.clusterCIDR}",
        f"--service-cidr={cluster.spec.k3s.serviceCIDR}",
        f"--node-ip={server.ip}",
        # The API certificate must be valid for every address the cluster is
        # reached by. There is no DNS in this lab, so the IP is the primary SAN;
        # the tailnet name is added now so the cert does not need reissuing when
        # the Tailscale API proxy comes up later.
        f"--tls-san={server.ip}",
        f"--tls-san={server.name}",
    ]
    return args


def server_install_script(cluster: Cluster) -> str:
    exec_args = " ".join(server_exec_args(cluster))
    return (
        f"curl -sfL {K3S_INSTALL_URL} | "
        f"INSTALL_K3S_VERSION={shlex.quote(cluster.spec.k3s.version)} "
        f"INSTALL_K3S_EXEC={shlex.quote(exec_args)} "
        f"sh -s -"
    )


def agent_install_script(cluster: Cluster, node: Node, token: str) -> str:
    if node.is_server:
        raise HomelabError(f"{node.name} has role: server — it cannot join as an agent")
    server_url = f"https://{cluster.server.ip}:6443"
    return (
        f"curl -sfL {K3S_INSTALL_URL} | "
        f"INSTALL_K3S_VERSION={shlex.quote(cluster.spec.k3s.version)} "
        f"K3S_URL={shlex.quote(server_url)} "
        f"K3S_TOKEN={shlex.quote(token)} "
        # Set explicitly so node names match cluster.yaml rather than whatever
        # the host happens to call itself.
        f"K3S_NODE_NAME={shlex.quote(node.name)} "
        f"sh -s - --node-ip={node.ip}"
    )


def uninstall_script(node: Node) -> str:
    """Servers and agents have DIFFERENT uninstall scripts. Running the server one
    on an agent silently does nothing and leaves a half-joined node behind — a
    mistake the archived v1 scripts made."""
    script = (
        "/usr/local/bin/k3s-uninstall.sh"
        if node.is_server
        else "/usr/local/bin/k3s-agent-uninstall.sh"
    )
    # Absent script means k3s was never installed, which is success for a teardown.
    return f"if [ -x {script} ]; then {script}; else echo 'k3s not installed'; fi"


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------

def is_installed(runner: Runner) -> bool:
    return runner.run(["test", "-x", "/usr/local/bin/k3s"], check=False, mutates=False).ok


def install_server(runner: Runner, cluster: Cluster) -> None:
    runner.run(["bash", "-c", server_install_script(cluster)], sudo=True, timeout=900)


def read_token(runner: Runner) -> str:
    token = runner.run(["cat", SERVER_TOKEN_PATH], sudo=True).out
    if not token:
        raise HomelabError(
            f"{SERVER_TOKEN_PATH} is empty on {runner.host}; the server did not finish installing"
        )
    return token


def join_agent(runner: Runner, cluster: Cluster, node: Node, token: str) -> None:
    runner.run(
        ["bash", "-c", agent_install_script(cluster, node, token)], sudo=True, timeout=900
    )


def uninstall(runner: Runner, node: Node) -> None:
    runner.run(["bash", "-c", uninstall_script(node)], sudo=True, timeout=600)


def fetch_kubeconfig(runner: Runner, cluster: Cluster) -> str:
    """Read the server's kubeconfig and point it at the node's real address.

    k3s writes `server: https://127.0.0.1:6443`, which is correct on the node and
    useless from anywhere else.
    """
    raw = runner.run(["cat", KUBECONFIG_PATH], sudo=True).stdout
    if "127.0.0.1" not in raw and "localhost" not in raw:
        return raw
    return raw.replace("https://127.0.0.1:6443", f"https://{cluster.server.ip}:6443").replace(
        "https://localhost:6443", f"https://{cluster.server.ip}:6443"
    )
