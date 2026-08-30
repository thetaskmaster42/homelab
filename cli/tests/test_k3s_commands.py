"""Golden tests on the exact commands sent to the nodes.

This is the highest-value suite in the CLI. The k3s flags encode three
deliberate decisions — flannel as the CNI, MetalLB instead of servicelb, and a
GitOps-managed Traefik instead of the bundled one — and every one of them is a
silent, hard-to-diagnose regression if dropped. Asserting the literal command
string catches that in milliseconds.

Two flags are notable by their ABSENCE, and the suite should be read with that
in mind. Adding `--flannel-backend=none` leaves the cluster with no CNI at all.
Adding `--disable-network-policy` turns off kube-router and leaves the ArgoCD
chart's six NetworkPolicies in the API with nothing enforcing them — no error,
no event, a policy that fails open. Both are silent.
See docs/decisions/0011-flannel-over-calico.md.
"""

from __future__ import annotations

import pytest

from homelab.config import Cluster
from homelab.errors import HomelabError
from homelab.steps import k3s

from conftest import assert_golden


def test_server_install_script_matches_golden(cluster):
    assert_golden("server_install.txt", k3s.server_install_script(cluster))


def test_agent_install_script_matches_golden(cluster):
    node = cluster.node("k3s-worker-1")
    assert_golden("agent_join.txt", k3s.agent_install_script(cluster, node, "K10test::token"))


@pytest.mark.parametrize(
    "flag",
    [
        "--flannel-backend=vxlan",    # k3s's bundled CNI
        "--disable=servicelb",        # MetalLB provides LoadBalancer IPs
        "--disable=traefik",          # Traefik is a config-driven Helm service
        "--write-kubeconfig-mode=644",
    ],
)
def test_load_bearing_server_flags_present(cluster, flag):
    assert flag in k3s.server_exec_args(cluster)


def test_server_binds_its_own_ip_and_cidrs(cluster):
    args = k3s.server_exec_args(cluster)
    assert "--node-ip=192.168.11.7" in args
    assert "--tls-san=192.168.11.7" in args
    assert "--cluster-cidr=10.42.0.0/16" in args
    assert "--service-cidr=10.43.0.0/16" in args


def test_agent_points_at_the_server_and_names_itself(cluster):
    node = cluster.node("k3s-worker-2")
    script = k3s.agent_install_script(cluster, node, "tok")
    assert "K3S_URL=https://192.168.11.7:6443" in script
    # Without K3S_NODE_NAME the node registers under whatever hostname it has,
    # which then does not match cluster.yaml.
    assert "K3S_NODE_NAME=k3s-worker-2" in script
    assert "--node-ip=192.168.11.5" in script


def test_agent_script_refuses_the_server_node(cluster):
    with pytest.raises(HomelabError, match="cannot join as an agent"):
        k3s.agent_install_script(cluster, cluster.server, "tok")


def test_token_is_shell_quoted(cluster):
    """k3s tokens contain '::'. An unquoted one with a shell metacharacter would
    be an injection into a sudo'd command."""
    node = cluster.node("k3s-worker-1")
    script = k3s.agent_install_script(cluster, node, "abc; rm -rf /")
    assert "'abc; rm -rf /'" in script
    assert "; rm -rf /" not in script.replace("'abc; rm -rf /'", "")


def test_version_is_pinned_in_both_scripts(cluster):
    version = cluster.spec.k3s.version
    assert version in k3s.server_install_script(cluster)
    assert version in k3s.agent_install_script(cluster, cluster.node("k3s-worker-1"), "t")


def test_uninstall_uses_the_right_script_per_role(cluster):
    """Servers and agents have different uninstall scripts. Using the server's on
    an agent silently does nothing and leaves a half-joined node."""
    assert "k3s-uninstall.sh" in k3s.uninstall_script(cluster.server)
    assert "k3s-agent-uninstall.sh" not in k3s.uninstall_script(cluster.server)

    agent = k3s.uninstall_script(cluster.node("k3s-worker-1"))
    assert "k3s-agent-uninstall.sh" in agent


def test_uninstall_is_idempotent_when_k3s_is_absent(cluster):
    assert "if [ -x" in k3s.uninstall_script(cluster.server)


def test_kubeconfig_server_address_is_rewritten(cluster):
    from homelab.runner import RecordingRunner, Result

    raw = "apiVersion: v1\nclusters:\n- cluster:\n    server: https://127.0.0.1:6443\n"
    runner = RecordingRunner(responses={"cat /etc/rancher/k3s/k3s.yaml": Result(0, raw, "")})
    out = k3s.fetch_kubeconfig(runner, cluster)
    # 127.0.0.1 is correct on the node and useless anywhere else.
    assert "https://192.168.11.7:6443" in out
    assert "127.0.0.1" not in out


# --------------------------------------------------------------------------
# ArgoCD install command
# --------------------------------------------------------------------------

def test_argocd_chart_is_referenced_by_bare_name(cluster, tmp_path):
    """`repo/chart` is a local alias that only resolves after `helm repo add`.
    Combined with --repo, helm searches for a chart literally named
    "argo/argo-cd" and reports it missing — which reads like a bad version
    rather than a malformed reference, and cost a real debugging round."""
    from homelab.steps import argocd

    values = tmp_path / "values.yaml"
    values.write_text("{}\n")
    argv = argocd.helm_install_argv(cluster, values)

    chart = argv[argv.index("--install") + 2]
    assert "/" not in chart, (
        f"chart reference {chart!r} uses the repo-alias form; with --repo it must be bare"
    )
    assert chart == "argo-cd"


def test_argocd_version_comes_from_cluster_config(cluster, tmp_path):
    from homelab.steps import argocd

    values = tmp_path / "values.yaml"
    values.write_text("{}\n")
    argv = argocd.helm_install_argv(cluster, values)
    assert argv[argv.index("--version") + 1] == cluster.spec.argocd.chartVersion


# --------------------------------------------------------------------------
# CNI cleanup on teardown
# --------------------------------------------------------------------------

def test_cni_cleanup_removes_the_dataplane_k3s_leaves_behind():
    """k3s-uninstall.sh does not touch a CNI it did not install.

    Measured after removing Calico: vxlan.calico still up on all three nodes,
    stale routes including IPAM blackholes, and 1249/292/210 iptables rules
    still loaded. A reboot clears it, but a teardown that needs a reboot to
    finish is not a teardown.
    """
    script = k3s.cni_cleanup_script()

    for device in ("vxlan.calico", "flannel.1", "cni0", "tunl0"):
        assert device in script, f"{device} is not cleaned up"
    assert "blackhole" in script, "Calico's IPAM blackhole routes are not removed"
    assert "ipset destroy" in script
    assert "/var/lib/calico" in script

    # The jumps out of the builtin chains must go before the chains themselves,
    # or every delete fails on "chain is still referenced".
    assert script.index("sed 's/^-A/-D/'") < script.index("-X"), (
        "custom chains are deleted before the rules referencing them are removed"
    )

    # Backreferences would be eaten by Python's own escape handling long before
    # sed saw them -- \\1 is an octal escape in a non-raw string. This caught a
    # real bug where both extractors silently matched nothing.
    assert "\\1" not in script, "sed backreference will not survive embedding in Python"
    assert "\x01" not in script, "a \\1 was already interpreted as an octal escape"

    # A nuke must not fail because something was already gone.
    assert "set +e" in script and script.rstrip().endswith("exit 0")


def test_cni_cleanup_runs_after_the_uninstall_not_before(monkeypatch):
    """Order matters: k3s actively recreates these while it is still running."""
    calls: list[str] = []

    class Recorder:
        def run(self, argv, **kw):
            calls.append(argv[-1])
            return type("R", (), {"ok": True, "out": "", "stderr": ""})()

    node = type("N", (), {"name": "n", "is_server": False})()
    k3s.uninstall(Recorder(), node)

    assert len(calls) == 2
    assert "k3s-agent-uninstall.sh" in calls[0]
    assert "cni cleanup done" in calls[1]
