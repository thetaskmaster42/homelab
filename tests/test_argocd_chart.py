"""The ArgoCD chart is installed by the CLI, not by an ApplicationSet.

That left a real gap: every chart under infra/services/ is rendered and
version-checked by CI, but the one chart the whole platform bootstraps from was
never validated anywhere. A malformed reference in the CLI surfaced only when
`homelab bootstrap` was run against real hardware, and helm's error ("version
not found") pointed at the version rather than the actual fault.

These checks close that gap: the chart named in clusters/*/cluster.yaml must
exist at the pinned version and must render with the values file the CLI passes.
"""

from __future__ import annotations

import subprocess

import pytest
import yaml

from conftest import REPO, needs_helm

pytestmark = pytest.mark.network

CLUSTERS = sorted((REPO / "clusters").glob("*/cluster.yaml"))
ARGO_REPO = "https://argoproj.github.io/argo-helm"
ARGO_CHART = "argo-cd"


def _argocd_spec(path):
    return yaml.safe_load(path.read_text())["spec"]["argocd"]


@needs_helm
@pytest.mark.parametrize("cluster_file", CLUSTERS, ids=lambda p: p.parent.name)
def test_pinned_argocd_chart_version_exists(cluster_file):
    spec = _argocd_spec(cluster_file)
    result = subprocess.run(
        [
            "helm", "show", "chart", ARGO_CHART,
            "--repo", ARGO_REPO,
            "--version", str(spec["chartVersion"]),
        ],
        capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 0, (
        f"argocd.chartVersion {spec['chartVersion']!r} in {cluster_file.relative_to(REPO)} "
        f"does not resolve:\n{result.stderr.strip()}"
    )


@needs_helm
@pytest.mark.parametrize("cluster_file", CLUSTERS, ids=lambda p: p.parent.name)
def test_argocd_renders_with_the_values_the_cli_passes(cluster_file):
    spec = _argocd_spec(cluster_file)
    values = REPO / spec["valuesFile"]
    assert values.is_file(), f"argocd.valuesFile {spec['valuesFile']} does not exist"

    result = subprocess.run(
        [
            "helm", "template", "argocd", ARGO_CHART,
            "--repo", ARGO_REPO,
            "--version", str(spec["chartVersion"]),
            "--namespace", "argocd",
            "-f", str(values),
        ],
        capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0, (
        f"ArgoCD does not render with {spec['valuesFile']}:\n{result.stderr.strip()}"
    )
    assert "kind: Deployment" in result.stdout


@pytest.mark.parametrize("cluster_file", CLUSTERS, ids=lambda p: p.parent.name)
def test_root_application_and_values_paths_resolve(cluster_file):
    """Both are referenced by `homelab bootstrap`; a typo here fails only on real
    hardware, halfway through a rebuild."""
    spec = _argocd_spec(cluster_file)
    for key in ("valuesFile", "rootApp"):
        target = REPO / spec[key]
        assert target.is_file(), f"argocd.{key} points at a missing file: {spec[key]}"
