"""`.sops.yaml` must actually cover the files people are told to create.

A creation rule that matches nothing fails with
`error loading config: no matching creation rules found`, which names the config
rather than the path — so the natural reaction is to edit `.sops.yaml`, which is
usually not the problem. Checking the coupling here is cheaper than rediscovering
it at the moment someone is holding a real credential.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from conftest import REPO

SOPS_CONFIG = REPO / ".sops.yaml"
PLACEHOLDER = "REPLACE_WITH_YOUR_AGE_PUBLIC_KEY"


def rules() -> list[dict]:
    doc = yaml.safe_load(SOPS_CONFIG.read_text()) or {}
    return doc.get("creation_rules") or []


def test_config_exists_and_has_rules():
    assert SOPS_CONFIG.is_file(), "no .sops.yaml at the repo root"
    assert rules(), ".sops.yaml defines no creation_rules"


@pytest.mark.parametrize(
    "path",
    [
        "clusters/rps/bootstrap-secrets.enc.yaml",
        "infra/services/tailscale-operator/secrets.enc.yaml",
    ],
)
def test_documented_paths_match_a_creation_rule(path):
    """Every path the docs tell someone to create must be covered, whether or not
    it exists yet."""
    matched = any(re.search(rule["path_regex"], path) for rule in rules() if "path_regex" in rule)
    assert matched, (
        f"no creation rule in .sops.yaml matches {path!r}. Encrypting it would fail with "
        f"'no matching creation rules found'."
    )


def test_only_secret_values_are_encrypted():
    """Encrypting whole documents makes every diff opaque — you could not review a
    namespace change without decrypting. Restricting it to data/stringData keeps
    structure readable while the payload stays sealed."""
    for rule in rules():
        assert "encrypted_regex" in rule, (
            f"rule {rule.get('path_regex')!r} encrypts entire documents; "
            f"set encrypted_regex to '^(data|stringData)$'"
        )


def test_age_recipient_is_a_public_key_or_an_obvious_placeholder():
    """A private key here would be catastrophic on a public repo; age private keys
    start with AGE-SECRET-KEY."""
    for rule in rules():
        recipient = str(rule.get("age", "")).strip()
        assert "AGE-SECRET-KEY" not in recipient, (
            ".sops.yaml contains an age PRIVATE key. Only the public key (age1...) "
            "belongs here. Rotate it immediately."
        )
        if recipient == PLACEHOLDER:
            pytest.skip("placeholder — the operator has not generated a key yet")
        assert recipient.startswith("age1"), (
            f"age recipient {recipient!r} is not a public key (expected age1...)"
        )
