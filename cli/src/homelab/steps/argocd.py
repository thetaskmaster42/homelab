"""Install ArgoCD and hand it the repo.

This is the CLI's last act. Everything past the root Application is git's
business, and nothing in this module knows what infra/services or apps contain —
deliberately, because the moment the CLI knows about a service, adding a service
starts requiring a CLI release.

`bootstrap` is separate from `install` and safe to re-run, so it doubles as
break-glass recovery when ArgoCD has broken its own ability to sync.
"""

from __future__ import annotations

from pathlib import Path

from ..config import Cluster
from ..errors import HomelabError
from ..runner import Runner

ARGO_REPO = "https://argoproj.github.io/argo-helm"
NAMESPACE = "argocd"


def helm_install_argv(cluster: Cluster, values_file: Path) -> list[str]:
    return [
        "helm", "upgrade", "--install", "argocd", "argo/argo-cd",
        "--repo", ARGO_REPO,
        "--version", cluster.spec.argocd.chartVersion,
        "--namespace", NAMESPACE,
        "--create-namespace",
        "-f", str(values_file),
        "--wait",
        "--timeout", "10m",
    ]


def is_installed(runner: Runner) -> bool:
    return runner.run(
        ["kubectl", "-n", NAMESPACE, "get", "deploy", "argocd-server"], check=False, mutates=False
    ).ok


def install(runner: Runner, cluster: Cluster, repo_root: Path) -> None:
    values = repo_root / cluster.spec.argocd.valuesFile
    if not values.is_file():
        raise HomelabError(f"ArgoCD values file not found: {values}")
    runner.run(helm_install_argv(cluster, values), timeout=900)


def apply_root_app(runner: Runner, cluster: Cluster, repo_root: Path) -> None:
    root = repo_root / cluster.spec.argocd.rootApp
    if not root.is_file():
        raise HomelabError(f"root Application not found: {root}")
    runner.run(["kubectl", "apply", "-f", str(root)], timeout=120)


def initial_password(runner: Runner) -> str:
    result = runner.run(
        [
            "kubectl", "-n", NAMESPACE, "get", "secret", "argocd-initial-admin-secret",
            "-o", "jsonpath={.data.password}",
        ],
        check=False,
        mutates=False,
    )
    if not result.ok or not result.out:
        return ""
    import base64

    return base64.b64decode(result.out).decode(errors="replace")


def applications(runner: Runner) -> list[dict]:
    """Every Application ArgoCD knows about, with its sync and health state."""
    result = runner.run(
        ["kubectl", "-n", NAMESPACE, "get", "applications", "-o", "json"],
        check=False,
        mutates=False,
    )
    if not result.ok:
        return []
    import json

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    out = []
    for item in payload.get("items", []):
        status = item.get("status", {})
        out.append(
            {
                "name": item["metadata"]["name"],
                "sync": status.get("sync", {}).get("status", "Unknown"),
                "health": status.get("health", {}).get("status", "Unknown"),
            }
        )
    return sorted(out, key=lambda a: a["name"])
