from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from homelab.config import Cluster

REPO = Path(__file__).resolve().parents[2]
GOLDEN = Path(__file__).parent / "golden"

# The real cluster config, so the tests exercise what actually ships rather than
# a fixture that can drift away from it.
REAL_CONFIG = REPO / "clusters" / "rps" / "cluster.yaml"


@pytest.fixture(scope="session")
def raw_config() -> dict:
    return yaml.safe_load(REAL_CONFIG.read_text())


@pytest.fixture
def cfg(raw_config) -> dict:
    """A mutable deep copy, so a test can break one field without affecting others."""
    return copy.deepcopy(raw_config)


@pytest.fixture
def cluster(raw_config) -> Cluster:
    return Cluster.model_validate(copy.deepcopy(raw_config))


def assert_golden(name: str, actual: str) -> None:
    """Compare against a checked-in golden file.

    Regenerate deliberately with UPDATE_GOLDEN=1, never casually: these files are
    the record of which k3s flags are load-bearing, and an unreviewed update
    defeats the point.
    """
    import os

    path = GOLDEN / name
    if os.environ.get("UPDATE_GOLDEN"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(actual if actual.endswith("\n") else actual + "\n")
        return
    assert path.is_file(), f"missing golden file {path}; regenerate with UPDATE_GOLDEN=1"
    expected = path.read_text().rstrip("\n")
    assert actual.rstrip("\n") == expected, (
        f"{name} changed.\n\nexpected:\n{expected}\n\nactual:\n{actual}"
    )
