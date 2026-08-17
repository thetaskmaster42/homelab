"""A chart must render identically twice, or it cannot be GitOps-managed.

Charts that call `randAlphaNum` without a lookup guard produce fresh passwords
and fresh resource names on every render. ArgoCD re-renders on every refresh, so
such a chart is permanently OutOfSync — and under `automated` + `selfHeal` it
does real damage: rotating a database password out from under a running database,
or repeatedly re-creating Jobs that turn out to be schema migrations.

This is what disqualified Devtron (docs/decisions/0005-headlamp-over-devtron.md).
It passed the arm64 check comfortably; it failed this one. Without this test the
problem surfaces as a mysterious permanently-OutOfSync Application weeks later.
"""

from __future__ import annotations

import difflib
import re
import subprocess

import pytest

from conftest import needs_helm

pytestmark = pytest.mark.network

# Kubernetes stamps some values at apply time; ignore those rather than the
# chart's own nondeterminism.
NOISE = re.compile(r"^\s*(creationTimestamp|resourceVersion|uid):", re.MULTILINE)


def render(service) -> str:
    result = subprocess.run(
        [
            "helm", "template", service.name,
            service.config["chart"]["name"],
            "--repo", service.config["chart"]["repo"],
            "--version", str(service.config["chart"]["version"]),
            "--namespace", service.config["namespace"],
            "-f", str(service.values_file),
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        pytest.fail(f"helm template failed for {service.name}:\n{result.stderr}")
    return NOISE.sub("", result.stdout)


@needs_helm
def test_chart_renders_deterministically(service):
    first = render(service)
    second = render(service)

    if first == second:
        return

    diff = list(
        difflib.unified_diff(
            first.splitlines(), second.splitlines(),
            fromfile="render-1", tofile="render-2", lineterm="", n=1,
        )
    )
    pytest.fail(
        f"{service.name}: the chart renders differently on consecutive runs, so ArgoCD "
        f"will report it permanently OutOfSync and selfHeal will churn it. Usually a "
        f"randAlphaNum without a lookup guard. Either pin the generated values through "
        f"values.yaml, or do not manage this chart with automated sync.\n"
        + "\n".join(diff[:40])
    )
