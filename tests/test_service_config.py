"""Schema and policy checks on infra/services/*/service.yaml.

These run in milliseconds and catch the mistakes that would otherwise surface as
a red Application in the ArgoCD UI minutes after a push.
"""

from __future__ import annotations

import re

import yaml

from conftest import REPO

FLOATING = {"*", "latest", "", None}
SEMVERISH = re.compile(r"^v?\d+\.\d+\.\d+")


def test_directory_name_matches_declared_name(service):
    assert service.config["name"] == service.name, (
        f"{service.dir}/service.yaml declares name={service.config['name']!r} but lives in "
        f"a directory named {service.name!r}. The ApplicationSet uses the declared name to "
        f"build the values path, so a mismatch produces a broken $values reference."
    )


def test_required_fields_present(service):
    for field in ("name", "namespace", "chart", "extraManifests"):
        assert field in service.config, f"{service.name}: service.yaml is missing {field!r}"
    for field in ("repo", "name", "version"):
        assert field in service.config["chart"], f"{service.name}: chart.{field} is missing"


def test_chart_version_is_pinned(service):
    """A floating chart version means an upstream release can change what is
    deployed with no commit here. That is the opposite of GitOps, and it is how
    the previous iteration of this repo accumulated four `targetRevision: '*'`
    placeholders that nobody ever went back to fix."""
    version = service.config["chart"]["version"]
    assert version not in FLOATING, f"{service.name}: chart version {version!r} is not pinned"
    assert SEMVERISH.match(str(version)), (
        f"{service.name}: chart version {version!r} does not look like a pinned release"
    )


def test_extra_manifests_flag_matches_reality(service):
    """The flag drives a post-selector on the companion ApplicationSet. If it
    says "true" with no manifests/ directory, ArgoCD generates an Application
    pointing at a path that does not exist; if it says "false" with one, the
    manifests are silently never deployed."""
    declared = str(service.config["extraManifests"]).lower() == "true"
    exists = service.manifests_dir.is_dir() and any(service.manifests_dir.glob("*.yaml"))
    assert declared == exists, (
        f"{service.name}: extraManifests={service.config['extraManifests']!r} but "
        f"manifests/ {'exists' if exists else 'does not exist'}"
    )


def test_values_file_exists_and_parses(service):
    assert service.values_file.is_file(), (
        f"{service.name}: values.yaml is missing. The ApplicationSet references it "
        f"unconditionally via $values, so its absence fails the sync."
    )
    yaml.safe_load(service.values_file.read_text())


def test_chart_repo_is_allowed_by_the_appproject(service):
    """ArgoCD refuses a source that is not in the AppProject's sourceRepos, and
    the resulting error is easy to misread as a network problem."""
    project = yaml.safe_load_all((REPO / "argocd/registry/projects.yaml").read_text())
    infra = next(p for p in project if p["metadata"]["name"] == "infra")
    allowed = set(infra["spec"]["sourceRepos"])
    repo = service.config["chart"]["repo"]
    assert repo in allowed, (
        f"{service.name}: chart repo {repo} is not in the infra AppProject sourceRepos. "
        f"Add it to argocd/registry/projects.yaml."
    )
