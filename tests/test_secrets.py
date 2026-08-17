"""Guardrails for a PUBLIC repository.

A plaintext credential pushed here is unrecoverable — it is cloned, cached and
indexed within minutes, and rotating it is the only real remedy. These checks are
the cheap first line of defence; `gitleaks` in CI is the second.

This suite runs before SOPS is wired up (M6), which is deliberate: the checks
must already be blocking by the time the first real credential exists.
"""

from __future__ import annotations

import re
import subprocess

import pytest
import yaml

from conftest import REPO

# Keys whose values would be credentials if they were non-empty literals.
SENSITIVE_KEYS = {"clientSecret", "password", "token", "apiKey", "privateKey", "secretKey"}

PLACEHOLDER = {"", None, "REPLACE_ME", "changeme"}

SECRETISH_FILENAME = re.compile(r"(secret|credential|token|password)", re.IGNORECASE)


def tracked_yaml_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "*.yaml", "*.yml"], cwd=REPO, capture_output=True, text=True
    )
    return [line for line in out.stdout.splitlines() if line]


@pytest.mark.parametrize("relpath", tracked_yaml_files())
def test_no_committed_secret_objects_with_data(relpath):
    """A Kubernetes Secret with literal data must never be committed in the
    clear. Once SOPS lands these live in *.enc.yaml with an encrypted payload."""
    path = REPO / relpath
    if relpath.endswith(".enc.yaml"):
        pytest.skip("encrypted file")

    try:
        docs = list(yaml.safe_load_all(path.read_text()))
    except yaml.YAMLError:
        pytest.skip(f"{relpath} is not plain YAML")

    for doc in docs:
        if not isinstance(doc, dict) or doc.get("kind") != "Secret":
            continue
        payload = {**(doc.get("data") or {}), **(doc.get("stringData") or {})}
        populated = {k: v for k, v in payload.items() if v not in PLACEHOLDER}
        assert not populated, (
            f"{relpath} commits a Secret with literal values for {sorted(populated)}. "
            f"Encrypt it as a *.enc.yaml instead."
        )


@pytest.mark.parametrize("relpath", tracked_yaml_files())
def test_no_populated_sensitive_keys(relpath):
    """Catches credentials smuggled into Helm values, where they would not look
    like a Secret object at all."""
    path = REPO / relpath
    if relpath.endswith(".enc.yaml"):
        pytest.skip("encrypted file")

    findings: list[str] = []

    def walk(node, trail=""):
        if isinstance(node, dict):
            for key, value in node.items():
                here = f"{trail}.{key}" if trail else key
                if key in SENSITIVE_KEYS and isinstance(value, str) and value not in PLACEHOLDER:
                    findings.append(here)
                walk(value, here)
        elif isinstance(node, list):
            for i, item in enumerate(node):
                walk(item, f"{trail}[{i}]")

    try:
        for doc in yaml.safe_load_all(path.read_text()):
            walk(doc)
    except yaml.YAMLError:
        pytest.skip(f"{relpath} is not plain YAML")

    assert not findings, f"{relpath}: sensitive key(s) hold a literal value: {findings}"


def test_secretish_filenames_are_encrypted():
    """Anything named like a secret must be an encrypted file, so that a future
    `secrets.yaml` cannot quietly appear alongside `secrets.enc.yaml`."""
    offenders = [
        f
        for f in tracked_yaml_files()
        if SECRETISH_FILENAME.search(f.rsplit("/", 1)[-1]) and not f.endswith(".enc.yaml")
    ]
    assert not offenders, (
        f"files named like secrets but not encrypted: {offenders}. "
        f"Rename to *.enc.yaml and encrypt with sops."
    )


def test_gitignore_covers_key_material():
    ignored = (REPO / ".gitignore").read_text()
    for pattern in ("*.agekey", "keys.txt", "kubeconfig", ".homelab/"):
        assert pattern in ignored, f".gitignore is missing {pattern!r}"
