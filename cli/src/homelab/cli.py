"""homelab — build the k3s cluster, then get out of the way.

The boundary this CLI respects: it owns everything that must exist before the
Kubernetes API is usable, and nothing after. It never installs an infra service
and never applies an application manifest. If you find yourself wanting to add a
per-service step here, that change belongs in infra/services/ instead.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import sys
import time
from pathlib import Path

from . import config as config_mod
from . import state as state_mod
from .config import Cluster, Node
from .errors import AuthError, HomelabError, Unreachable
from .runner import LocalRunner, Runner, SSHRunner
from .steps import argocd, cni, k3s, preflight

DEFAULT_CLUSTER = "rps"


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

def _tty() -> bool:
    return sys.stdout.isatty()


def say(msg: str = "") -> None:
    print(msg, flush=True)


def step(msg: str) -> None:
    say(f"\n\033[1m==>\033[0m {msg}" if _tty() else f"\n==> {msg}")


def ok(msg: str) -> None:
    say(f"  \033[32mok\033[0m   {msg}" if _tty() else f"  ok   {msg}")


def warn(msg: str) -> None:
    say(f"  \033[33mwarn\033[0m {msg}" if _tty() else f"  warn {msg}")


def bad(msg: str) -> None:
    say(f"  \033[31mFAIL\033[0m {msg}" if _tty() else f"  FAIL {msg}")


# --------------------------------------------------------------------------
# Wiring
# --------------------------------------------------------------------------

def repo_root() -> Path:
    # cli/src/homelab/cli.py -> repo root is three parents up from the package.
    return Path(__file__).resolve().parents[3]


def ssh_to(cluster: Cluster, node: Node, dry_run: bool = False) -> SSHRunner:
    return SSHRunner(node=node, ssh=cluster.spec.ssh, dry_run=dry_run)


def kube(cluster: Cluster, dry_run: bool = False) -> LocalRunner:
    """Local kubectl/helm for this cluster.

    Prefers the kubeconfig `install` fetched, but falls back to the ambient one
    when that file does not exist — so `status` is useful against a cluster this
    CLI did not build, which is the normal case before the first rebuild.
    """
    path = kubeconfig_path(cluster)
    env = {"KUBECONFIG": str(path)} if path.is_file() else None
    return LocalRunner(host="local", dry_run=dry_run, env=env)


def kubeconfig_path(cluster: Cluster) -> Path:
    return repo_root() / ".homelab" / f"kubeconfig-{cluster.name}"


def load_cluster(args) -> Cluster:
    return config_mod.find(repo_root(), args.cluster)


# --------------------------------------------------------------------------
# init
# --------------------------------------------------------------------------

def cmd_init(args) -> int:
    cluster = load_cluster(args)
    say(f"Preflight for cluster {cluster.name!r} — nothing will be changed.")

    failures = 0

    step("Local tooling")
    for check in preflight.check_local(cluster):
        (ok if check.ok else bad)(f"{check.name:<14} {check.detail}")
        failures += not check.ok

    local_epoch = int(time.time())
    unreachable: list[str] = []

    for node in cluster.nodes:
        step(f"{node.name} ({node.ip}, {node.role})")
        runner = ssh_to(cluster, node)
        checks = preflight.check_node(runner, node, local_epoch=local_epoch)
        if len(checks) == 1 and checks[0].name == "ssh" and not checks[0].ok:
            if checks[0].fatal:
                # The host answered and refused the key. Waiting will not fix it,
                # so this is a failure rather than a node that is merely absent.
                bad(f"ssh            {checks[0].detail}")
                bad(f"{'':<14} the host is up but will not accept "
                    f"{cluster.spec.ssh.identity_path}")
                bad(f"{'':<14} fix: ssh-copy-id -i {cluster.spec.ssh.identity_path}.pub "
                    f"{cluster.spec.ssh.user}@{node.ip}")
                failures += 1
                continue
            # Nothing answered. A node that is not built yet is an expected
            # state: k3s-worker-2 joins after the clean install by design.
            warn(f"unreachable — {checks[0].detail}")
            warn("if this node is not built yet, that is fine; install will skip it")
            unreachable.append(node.name)
            continue
        for check in checks:
            (ok if check.ok else bad)(f"{check.name:<14} {check.detail}")
            failures += not check.ok

    step("Summary")
    if unreachable:
        warn(f"unreachable (will be skipped): {', '.join(unreachable)}")
    if failures:
        bad(f"{failures} check(s) failed on reachable nodes")
        return 1
    if unreachable and all(n.name in unreachable for n in cluster.agents):
        warn("no agents reachable — install would build a single-node cluster")
    ok("ready to install")
    return 0


# --------------------------------------------------------------------------
# install
# --------------------------------------------------------------------------

def cmd_install(args) -> int:
    cluster = load_cluster(args)
    state = state_mod.load(repo_root(), cluster.name)
    state.k3sVersion = cluster.spec.k3s.version

    server = cluster.server
    server_runner = ssh_to(cluster, server, dry_run=args.dry_run)

    step(f"Control plane: {server.name} ({server.ip})")
    if k3s.is_installed(server_runner) and not args.force:
        ok("k3s already installed — skipping (use --force to reinstall)")
    else:
        say("  installing k3s server...")
        k3s.install_server(server_runner, cluster)
        ok(f"k3s {cluster.spec.k3s.version} installed")
    state.mark(server.name, phase="installed", ip=str(server.ip), role="server")

    step("Kubeconfig")
    if args.dry_run:
        ok("skipped (dry run)")
    else:
        raw = k3s.fetch_kubeconfig(server_runner, cluster)
        dest = kubeconfig_path(cluster)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(raw)
        dest.chmod(0o600)
        ok(f"written to {dest}")
        say(f"       use it with:  export KUBECONFIG={dest}")

    kubectl = kube(cluster, dry_run=args.dry_run)

    step(f"CNI: {cluster.spec.cni.provider} {cluster.spec.cni.version}")
    if cluster.spec.cni.provider == "none":
        warn("cni.provider is 'none' — no pod will schedule until one is installed")
    elif cni.is_installed(kubectl) and not args.force:
        ok("already installed")
    else:
        say("  applying tigera-operator, then the Installation CR (retrying for CRDs)...")
        cni.install(kubectl, cluster)
        ok("Calico applied")

    # --- agents -----------------------------------------------------------
    targets = cluster.agents
    if args.node:
        targets = [cluster.node(n) for n in args.node if not cluster.node(n).is_server]

    if targets:
        step("Agents")
        token = "" if args.dry_run else k3s.read_token(server_runner)
        if token:
            state.tokenFingerprint = state_mod.fingerprint(token)

        results = _join_agents(cluster, targets, token, dry_run=args.dry_run, force=args.force)
        for node_name, status, detail in results:
            if status == "joined":
                ok(f"{node_name:<14} joined")
                state.mark(node_name, phase="joined", role="agent")
            elif status == "present":
                ok(f"{node_name:<14} already joined")
                state.mark(node_name, phase="joined", role="agent")
            elif status == "pending":
                warn(f"{node_name:<14} unreachable — skipped, join it later with:")
                warn(f"{'':<14}   homelab install --node {node_name}")
                state.mark(node_name, phase="pending", role="agent", note=detail)
            else:
                bad(f"{node_name:<14} {detail}")
                state.mark(node_name, phase="failed", role="agent", note=detail)

    step("Waiting for nodes to become Ready")
    if args.dry_run:
        ok("skipped (dry run)")
    else:
        try:
            cni.wait_for_nodes_ready(kubectl)
            ok("all nodes Ready")
        except HomelabError as exc:
            warn(f"not all nodes became Ready: {exc}")

    for node in cluster.nodes:
        state.nodes.setdefault(node.name, state_mod.NodeState(ip=str(node.ip), role=node.role))

    step("Done")
    if args.dry_run:
        # A dry run must not leave anything behind, state file included.
        ok("dry run — no state written")
    else:
        ok(f"state written to {state_mod.save(repo_root(), state)}")
    if state.pending:
        warn(f"pending nodes: {', '.join(state.pending)}")
        warn("re-run `homelab install` once they are built; nothing else needs redoing")
    say("\nNext:  homelab bootstrap")
    return 0


def _join_agents(
    cluster: Cluster, targets: list[Node], token: str, *, dry_run: bool, force: bool
) -> list[tuple[str, str, str]]:
    """Join agents in parallel. An unreachable agent is `pending`, not a failure —
    hardware that does not exist yet is an expected state here."""

    def one(node: Node) -> tuple[str, str, str]:
        runner = ssh_to(cluster, node, dry_run=dry_run)
        try:
            if k3s.is_installed(runner) and not force:
                return (node.name, "present", "")
            if dry_run:
                return (node.name, "joined", "")
            k3s.join_agent(runner, cluster, node, token)
            return (node.name, "joined", "")
        except AuthError as exc:
            # Up but refusing the key: a real failure, not a node awaiting build.
            return (node.name, "failed", f"key rejected — {exc.detail}")
        except Unreachable as exc:
            return (node.name, "pending", exc.detail or "no response")
        except HomelabError as exc:
            return (node.name, "failed", str(exc).splitlines()[0][:160])

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(len(targets), 1)) as pool:
        return sorted(pool.map(one, targets), key=lambda r: r[0])


# --------------------------------------------------------------------------
# bootstrap
# --------------------------------------------------------------------------

def cmd_bootstrap(args) -> int:
    cluster = load_cluster(args)
    state = state_mod.load(repo_root(), cluster.name)
    kubectl = kube(cluster, dry_run=args.dry_run)
    root = repo_root()

    step(f"ArgoCD {cluster.spec.argocd.chartVersion}")
    if argocd.is_installed(kubectl) and not args.force:
        ok("already installed — re-running helm upgrade to converge values")
    argocd.install(kubectl, cluster, root)
    ok("installed")

    step("Root Application")
    argocd.apply_root_app(kubectl, cluster, root)
    ok(f"applied {cluster.spec.argocd.rootApp}")
    say("       ArgoCD now owns infra/services/ and apps/. Git is the only input.")

    if not args.dry_run:
        state.argocdInstalled = True
        state_mod.save(root, state)

    if not args.dry_run:
        step("Access")
        password = argocd.initial_password(kubectl)
        if password:
            say(f"  admin password: {password}")
            say("  (delete the argocd-initial-admin-secret once you have changed it)")
        say("  kubectl -n argocd port-forward svc/argocd-server 8080:443")

    step("Still to do by hand")
    warn("bootstrap secrets are not applied yet — see docs/bootstrap.md:")
    warn("  age key, tailscale OAuth, grafana-admin")
    warn("monitoring will not sync until grafana-admin exists")
    return 0


# --------------------------------------------------------------------------
# status
# --------------------------------------------------------------------------

def cmd_status(args) -> int:
    cluster = load_cluster(args)
    state = state_mod.load(repo_root(), cluster.name)
    kubectl = kube(cluster)

    step(f"Cluster {cluster.name}")
    say(f"  k3s        {cluster.spec.k3s.version}")
    say(f"  kubeconfig {kubeconfig_path(cluster)}")
    if state.updatedAt:
        say(f"  state      {state.updatedAt}")

    step("Nodes (from cluster.yaml)")
    result = kubectl.run(
        [
            "kubectl", "get", "nodes",
            "-o", "jsonpath={range .items[*]}{.metadata.name}{\" \"}"
                  "{.status.conditions[?(@.type=='Ready')].status}{\"\\n\"}{end}",
        ],
        check=False,
    )
    live = {}
    if result.ok:
        for line in result.out.splitlines():
            parts = line.split()
            if len(parts) == 2:
                live[parts[0]] = parts[1]

    for node in cluster.nodes:
        status = live.get(node.name)
        label = f"{node.name:<14} {str(node.ip):<15} {node.role:<7}"
        if status == "True":
            ok(f"{label} Ready")
        elif status == "False":
            bad(f"{label} NotReady")
        elif status is None and state.phase(node.name) == "pending":
            warn(f"{label} pending — not built yet")
        elif status is None:
            warn(f"{label} absent from the cluster")
        else:
            warn(f"{label} {status}")

    step("ArgoCD applications")
    apps = argocd.applications(kubectl)
    if not apps:
        warn("none — ArgoCD not installed, or the root Application has not synced")
    for app in apps:
        line = f"{app['name']:<26} {app['sync']:<10} {app['health']}"
        if app["sync"] == "Synced" and app["health"] == "Healthy":
            ok(line)
        elif app["health"] == "Degraded" or app["sync"] == "Unknown":
            bad(line)
        else:
            warn(line)
    return 0


# --------------------------------------------------------------------------
# nuke
# --------------------------------------------------------------------------

def cmd_nuke(args) -> int:
    cluster = load_cluster(args)
    targets = [cluster.node(n) for n in args.node] if args.node else list(cluster.nodes)

    step("This will destroy")
    for node in targets:
        say(f"  {node.name:<14} {node.ip}  ({node.role})")

    # local-path PersistentVolumes live under /var/lib/rancher/k3s/storage and
    # are erased with the rest of it. Naming them is the difference between an
    # informed decision and an unpleasant surprise.
    kubectl = kube(cluster)
    pvcs = kubectl.run(
        [
            "kubectl", "get", "pvc", "-A",
            "-o", "jsonpath={range .items[*]}{.metadata.namespace}/{.metadata.name}"
                  "{\" \"}{.spec.storageClassName}{\"\\n\"}{end}",
        ],
        check=False,
    )
    doomed = [
        line for line in pvcs.out.splitlines() if line.strip() and "local-path" in line
    ]
    if doomed:
        say("\n  PersistentVolumeClaims on local-path — these will be LOST:")
        for line in doomed:
            bad(f"  {line}")
        say("  (PVCs on the nfs class survive; reclaimPolicy is Retain)")

    if not args.yes:
        say("")
        answer = input(f"Type the cluster name ({cluster.name}) to confirm: ").strip()
        if answer != cluster.name:
            say("aborted")
            return 1

    state = state_mod.load(repo_root(), cluster.name)
    # Agents first: uninstalling the server first strands them talking to nothing.
    for node in sorted(targets, key=lambda n: n.is_server):
        step(f"Uninstalling {node.name}")
        runner = ssh_to(cluster, node, dry_run=args.dry_run)
        try:
            k3s.uninstall(runner, node)
            ok("removed")
            state.mark(node.name, phase="removed", role=node.role)
        except Unreachable as exc:
            warn(f"unreachable, nothing done — {exc.detail}")
        except HomelabError as exc:
            bad(str(exc).splitlines()[0][:160])

    if not args.node:
        state.argocdInstalled = False
        state.tokenFingerprint = ""
        kc = kubeconfig_path(cluster)
        if kc.exists() and not args.dry_run:
            kc.unlink()
            ok(f"removed {kc}")
    if not args.dry_run:
        state_mod.save(repo_root(), state)
    say("\nRebuild with:  homelab install && homelab bootstrap")
    return 0


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="homelab",
        description="Build the k3s cluster from clusters/<name>/cluster.yaml. "
        "Stops once ArgoCD is running — everything after that is git.",
    )
    parser.add_argument("--cluster", default=DEFAULT_CLUSTER, help="cluster name under clusters/")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="preflight checks; changes nothing")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("install", help="k3s server, CNI, and agent joins")
    p.add_argument("--node", action="append", help="only this node (repeatable)")
    p.add_argument("--force", action="store_true", help="reinstall even if k3s is present")
    p.add_argument("--dry-run", action="store_true", help="print what would run")
    p.set_defaults(func=cmd_install)

    p = sub.add_parser("bootstrap", help="install ArgoCD and apply the root Application")
    p.add_argument("--force", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_bootstrap)

    p = sub.add_parser("status", help="node and ArgoCD application health")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("nuke", help="uninstall k3s from every node")
    p.add_argument("--node", action="append", help="only this node (repeatable)")
    p.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_nuke)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except HomelabError as exc:
        # Actionable problems get a message, not a traceback.
        print(f"\nerror: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
