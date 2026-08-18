"""Every image deployed here must publish a linux/arm64 manifest.

This is the single most valuable test in the repo. The cluster is 100% arm64 and
the development laptop is x86_64, so an amd64-only image looks fine everywhere
except production, where it fails as `exec format error` inside a
CrashLoopBackOff — a genuinely confusing symptom to trace back to its cause.

Devtron is the concrete reason this exists: its `dashboard` and `hyperion`
images are multi-arch, but at the time of writing the latest `kubelink` build is
amd64-only. Rather than reasoning about which components of which chart version
are safe, render the chart and ask the registry.
"""

from __future__ import annotations

import json
import re
import subprocess

import pytest
import yaml

from conftest import needs_docker, needs_helm

pytestmark = pytest.mark.network

IMAGE_KEY = re.compile(r"^\s*image:\s*[\"']?([^\s\"']+)[\"']?\s*$", re.MULTILINE)


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
    return result.stdout


def images_in(manifest_text: str) -> set[str]:
    found = set()
    for raw in IMAGE_KEY.findall(manifest_text):
        # Skip templated leftovers and bare digests.
        if "{{" in raw or raw.startswith("sha256:"):
            continue
        found.add(raw)
    return found


def architectures(image: str) -> set[str]:
    """Ask the registry what platforms this image supports."""
    result = subprocess.run(
        ["docker", "manifest", "inspect", image],
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        pytest.skip(f"could not inspect {image}: {result.stderr.strip()[:200]}")
    data = json.loads(result.stdout)
    # A manifest list enumerates platforms; a single manifest has none, which
    # means it is single-architecture.
    if "manifests" in data:
        return {m.get("platform", {}).get("architecture") for m in data["manifests"]}
    verbose = subprocess.run(
        ["docker", "manifest", "inspect", "--verbose", image],
        capture_output=True, text=True, timeout=120,
    )
    if verbose.returncode == 0:
        blob = json.loads(verbose.stdout)
        entries = blob if isinstance(blob, list) else [blob]
        return {e.get("Descriptor", {}).get("platform", {}).get("architecture") for e in entries}
    return set()


@needs_helm
@needs_docker
def test_service_images_support_arm64(service):
    found = images_in(render(service))
    assert found, f"{service.name}: no images found in the rendered chart — is the render empty?"

    amd64_only = []
    for image in sorted(found):
        arches = architectures(image)
        if arches and "arm64" not in arches:
            amd64_only.append(f"{image} -> {sorted(a for a in arches if a)}")

    assert not amd64_only, (
        f"{service.name}: image(s) without a linux/arm64 manifest, which cannot run on this "
        f"all-arm64 cluster:\n  " + "\n  ".join(amd64_only)
    )


@needs_docker
def test_app_images_support_arm64(app, tmp_path):
    """Same check for application overlays, rendered through kustomize."""
    result = subprocess.run(
        ["kubectl", "kustomize", str(app.dir)], capture_output=True, text=True, timeout=180
    )
    if result.returncode != 0:
        pytest.fail(f"kustomize build failed for {app.name}:\n{result.stderr}")

    offenders = []
    for image in sorted(images_in(result.stdout)):
        arches = architectures(image)
        if arches and "arm64" not in arches:
            offenders.append(f"{image} -> {sorted(a for a in arches if a)}")

    assert not offenders, f"{app.name}: image(s) without linux/arm64:\n  " + "\n  ".join(offenders)
