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
    """Tracked files PLUS untracked-but-not-ignored ones.

    Using `git ls-files` alone made results depend on whether a file happened to
    be staged: a new example file was invisible locally and then failed in CI.
    Including untracked files catches a mistake before it is even added, and
    makes local runs match CI.
    """
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "*.yaml", "*.yml"],
        cwd=REPO, capture_output=True, text=True,
    )
    return sorted({line for line in out.stdout.splitlines() if line})


def is_example(relpath: str) -> bool:
    """Templates are exempt from the literal-value rules and held to a stricter
    one instead: they must contain ONLY placeholders."""
    return relpath.endswith(".example.yaml")


@pytest.mark.parametrize("relpath", tracked_yaml_files())
def test_no_committed_secret_objects_with_data(relpath):
    """A Kubernetes Secret with literal data must never be committed in the
    clear. Once SOPS lands these live in *.enc.yaml with an encrypted payload."""
    path = REPO / relpath
    if relpath.endswith(".enc.yaml"):
        pytest.skip("encrypted file")
    if is_example(relpath):
        pytest.skip("template — covered by test_example_files_contain_only_placeholders")

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
    if is_example(relpath):
        pytest.skip("template — covered by test_example_files_contain_only_placeholders")

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
    """Anything named like a secret must be encrypted, so a future `secrets.yaml`
    cannot quietly appear next to `secrets.enc.yaml`.

    `*.example.yaml` is exempt because a template with placeholders is exactly
    how someone learns what to fill in — but it is then held to a stricter
    standard by the test below.
    """
    offenders = [
        f
        for f in tracked_yaml_files()
        if SECRETISH_FILENAME.search(f.rsplit("/", 1)[-1])
        and not f.endswith(".enc.yaml")
        and not f.endswith(".example.yaml")
    ]
    assert not offenders, (
        f"files named like secrets but not encrypted: {offenders}. "
        f"Rename to *.enc.yaml and encrypt with sops."
    )


PLACEHOLDER_MARKERS = ("CHANGE_ME", "REPLACE_", "EXAMPLE", "changeme", "<", "admin")


@pytest.mark.parametrize(
    "relpath", [f for f in tracked_yaml_files() if is_example(f)]
)
def test_example_files_contain_only_placeholders(relpath):
    """An example that someone filled in and committed is the exact accident
    this whole scheme exists to prevent, and it would look innocuous in review."""
    path = REPO / relpath
    findings = []
    for doc in yaml.safe_load_all(path.read_text()):
        if not isinstance(doc, dict) or doc.get("kind") != "Secret":
            continue
        payload = {**(doc.get("data") or {}), **(doc.get("stringData") or {})}
        for key, value in payload.items():
            text = str(value)
            if not any(marker in text for marker in PLACEHOLDER_MARKERS):
                findings.append(f"{key}={text!r}")
    assert not findings, (
        f"{relpath} looks like it holds real values, not placeholders: {findings}"
    )


def test_encrypted_files_are_actually_encrypted():
    """A *.enc.yaml without a sops block is plaintext wearing the wrong name.

    Checked per document, not per file. A bundle of several Secrets is the normal
    shape here, sops writes a `sops:` block into each one, and a file where only
    some documents got encrypted is exactly the dangerous middle state — it would
    look encrypted to anything that only inspected the first document.
    """
    for relpath in tracked_yaml_files():
        if not relpath.endswith(".enc.yaml"):
            continue
        docs = [d for d in yaml.safe_load_all((REPO / relpath).read_text()) if d]
        assert docs, f"{relpath} is empty — a failed `sops --encrypt >` leaves a 0-byte file"
        for i, doc in enumerate(docs):
            assert "sops" in doc, (
                f"{relpath} document {i + 1} of {len(docs)} has no sops metadata — "
                f"it is not encrypted."
            )


def test_gitignore_covers_key_material():
    ignored = (REPO / ".gitignore").read_text()
    for pattern in ("*.agekey", "keys.txt", "kubeconfig", ".homelab/"):
        assert pattern in ignored, f".gitignore is missing {pattern!r}"
