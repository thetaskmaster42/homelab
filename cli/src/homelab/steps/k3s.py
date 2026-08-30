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


def cni_cleanup_script() -> str:
    """Remove the CNI dataplane that k3s-uninstall.sh leaves behind.

    Neither k3s-uninstall.sh nor k3s-agent-uninstall.sh touches a CNI it did not
    install. After removing Calico this was measured on all three nodes: the
    vxlan.calico interface still up, stale routes including IPAM blackholes, and
    1249/292/210 iptables rules still loaded. Nothing persists them, so a reboot
    clears it — but a teardown that needs a reboot to actually finish is not a
    teardown, and the sediment stays invisible until it misleads whoever next
    reads `ip route` on a supposedly clean node.

    Deliberately no sed backreferences: this string is embedded in Python, and
    `\1` is an octal escape there long before sed ever sees it. awk and cut do
    the same job with nothing for Python to eat.

    Every command is best-effort. A nuke must not fail because an interface it
    meant to delete was already absent — that is the expected case on a node
    that was already clean.
    """
    return r"""
set +e

# Tunnel and bridge devices outlive the uninstall; per-pod veths go with pods.
for i in vxlan.calico flannel.1 flannel-v6.1 cni0 tunl0 kube-ipvs0 nodelocaldns; do
  ip link delete "$i" 2>/dev/null
done
for i in $(ip -o link show 2>/dev/null | awk -F': ' '{print $2}' | cut -d@ -f1 | grep '^cali'); do
  ip link delete "$i" 2>/dev/null
done

# Routes into the pod network, including the blackholes Calico's IPAM leaves for
# blocks it owned. Read from the live table rather than hardcoded, so a changed
# cluster-cidr cannot quietly skip them.
ip route show 2>/dev/null | grep -E 'vxlan\.calico|flannel\.1|cni0|^blackhole ' \
  | while read -r r; do ip route del $r 2>/dev/null; done

# iptables: remove the jumps out of the builtin chains FIRST — a custom chain
# cannot be deleted while anything still references it.
for t in filter nat mangle raw; do
  iptables-save -t "$t" 2>/dev/null \
    | grep -E '^-A (INPUT|OUTPUT|FORWARD|PREROUTING|POSTROUTING)' \
    | grep -E 'cali-|KUBE-ROUTER|KUBE-POD-FW|KUBE-NWPLCY|flannel' \
    | sed 's/^-A/-D/' \
    | while read -r rule; do iptables -t "$t" $rule 2>/dev/null; done
  for c in $(iptables-save -t "$t" 2>/dev/null | awk '/^:/{print substr($1,2)}' \
             | grep -E '^(cali-|KUBE-ROUTER|KUBE-POD-FW|KUBE-NWPLCY)'); do
    iptables -t "$t" -F "$c" 2>/dev/null
    iptables -t "$t" -X "$c" 2>/dev/null
  done
done

# ipsets are referenced by those rules, so they only destroy cleanly now.
for s in $(ipset list -n 2>/dev/null | grep -E '^cali|^KUBE-'); do
  ipset destroy "$s" 2>/dev/null
done

# State directories the k3s uninstall does not know about.
rm -rf /var/lib/calico /var/run/calico /etc/cni/net.d/*calico* /opt/cni/bin/calico* 2>/dev/null
echo "cni cleanup done"
exit 0
"""


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
    # After k3s is gone, not before: the cleanup deletes interfaces and rules
    # that k3s would otherwise be actively re-creating.
    runner.run(["bash", "-c", cni_cleanup_script()], sudo=True, timeout=120, check=False)


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
