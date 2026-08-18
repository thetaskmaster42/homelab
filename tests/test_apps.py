"""Checks on apps/ — the application registry and its overlays."""

from __future__ import annotations

import re
import subprocess

import pytest
import yaml

from conftest import needs_kubectl

SHA40 = re.compile(r"\b[0-9a-f]{40}\b")
VALID_EXPOSURE = {"tailnet", "funnel", "internal"}


def test_directory_name_matches_declared_name(app):
    assert app.config["name"] == app.name


def test_required_fields(app):
    for field in ("name", "namespace", "sourceRepo", "exposure"):
        assert field in app.config, f"{app.name}: app.yaml is missing {field!r}"


def test_exposure_is_recognised(app):
    exposure = app.config["exposure"]
    assert exposure in VALID_EXPOSURE, (
        f"{app.name}: exposure={exposure!r} is not one of {sorted(VALID_EXPOSURE)}"
    )


def test_remote_bases_are_pinned_to_a_commit(app):
    """A remote base on a branch means a push to the application's own repo
    silently changes what is deployed here, with no diff and no review. Pinning
    to a commit sha makes the promotion an explicit, reviewable event."""
    kustomization = app.dir / "kustomization.yaml"
    if not kustomization.is_file():
        pytest.skip(f"{app.name} has no kustomization.yaml")

    doc = yaml.safe_load(kustomization.read_text())
    for resource in doc.get("resources", []):
        if not resource.startswith("http") and "github.com" not in resource:
            continue  # local file
        assert SHA40.search(resource), (
            f"{app.name}: remote resource is not pinned to a 40-char commit sha:\n  {resource}"
        )


def test_image_tags_are_not_floating(app):
    kustomization = app.dir / "kustomization.yaml"
    if not kustomization.is_file():
        pytest.skip(f"{app.name} has no kustomization.yaml")

    doc = yaml.safe_load(kustomization.read_text())
    for image in doc.get("images", []):
        tag = str(image.get("newTag", ""))
        assert tag not in {"latest", "main", ""}, (
            f"{app.name}: image {image.get('name')} is pinned to {tag!r}. A floating tag "
            f"makes rollback impossible and hides what is actually running."
        )


@needs_kubectl
def test_overlay_renders(app):
    result = subprocess.run(
        ["kubectl", "kustomize", str(app.dir)], capture_output=True, text=True, timeout=180
    )
    assert result.returncode == 0, f"{app.name}: kustomize build failed:\n{result.stderr}"
    docs = [d for d in yaml.safe_load_all(result.stdout) if d]
    assert docs, f"{app.name}: overlay rendered nothing"

    for doc in docs:
        ns = doc.get("metadata", {}).get("namespace")
        if doc.get("kind") in {"Namespace", "ClusterRole", "ClusterRoleBinding"}:
            continue
        assert ns == app.config["namespace"], (
            f"{app.name}: {doc.get('kind')}/{doc.get('metadata', {}).get('name')} renders into "
            f"namespace {ns!r}, expected {app.config['namespace']!r}"
        )
