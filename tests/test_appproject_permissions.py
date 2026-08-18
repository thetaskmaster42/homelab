"""The AppProjects restrict what each plane may create. Those restrictions have
to agree with what the ApplicationSets actually ask for.

They did not: `apps` had an empty clusterResourceWhitelist while appset-apps
sets CreateNamespace=true, and a Namespace is cluster-scoped. Every application
failed to sync with "resource :Namespace is not permitted in project apps" —
a coupling between two files that nothing checked.
"""

from __future__ import annotations

import pytest
import yaml

from conftest import REPO

REGISTRY = REPO / "argocd" / "registry"


def _projects() -> dict:
    docs = yaml.safe_load_all((REGISTRY / "projects.yaml").read_text())
    return {d["metadata"]["name"]: d for d in docs if d}


def _appsets() -> list[dict]:
    out = []
    for path in REGISTRY.glob("appset-*.yaml"):
        doc = yaml.safe_load(path.read_text())
        if doc:
            out.append(doc)
    return out


@pytest.mark.parametrize("appset", _appsets(), ids=lambda d: d["metadata"]["name"])
def test_createnamespace_requires_namespace_to_be_whitelisted(appset):
    spec = appset["spec"]["template"]["spec"]
    sync_options = (spec.get("syncPolicy") or {}).get("syncOptions") or []
    if "CreateNamespace=true" not in sync_options:
        pytest.skip("this ApplicationSet does not create namespaces")

    project = _projects()[spec["project"]]
    whitelist = project["spec"].get("clusterResourceWhitelist") or []
    permitted = any(
        entry.get("kind") in ("Namespace", "*") for entry in whitelist
    )
    assert permitted, (
        f"{appset['metadata']['name']} sets CreateNamespace=true but AppProject "
        f"{spec['project']!r} does not permit the cluster-scoped Namespace resource. "
        f"Every Application it generates will fail to sync."
    )


@pytest.mark.parametrize("appset", _appsets(), ids=lambda d: d["metadata"]["name"])
def test_appset_targets_a_project_that_exists(appset):
    project = appset["spec"]["template"]["spec"]["project"]
    assert project in _projects(), (
        f"{appset['metadata']['name']} targets AppProject {project!r}, which is not "
        f"defined in argocd/registry/projects.yaml"
    )


def test_apps_project_still_forbids_crds():
    """The Namespace exception must not become a blanket allow — an application
    that needs a CRD is infrastructure."""
    whitelist = _projects()["apps"]["spec"].get("clusterResourceWhitelist") or []
    kinds = {e.get("kind") for e in whitelist}
    assert "*" not in kinds, "apps must not be allowed arbitrary cluster resources"
    assert "CustomResourceDefinition" not in kinds
