"""Shared discovery for the repo-level validation suite.

These tests validate the *declarative* half of the repo — infra/services and
apps. The CLI has its own unit suite under cli/tests.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
INFRA = REPO / "infra" / "services"
APPS = REPO / "apps"


@dataclass(frozen=True)
class Service:
    name: str
    dir: Path
    config: dict

    @property
    def values_file(self) -> Path:
        return self.dir / "values.yaml"

    @property
    def manifests_dir(self) -> Path:
        return self.dir / "manifests"

    def __str__(self) -> str:  # readable test ids
        return self.name


@dataclass(frozen=True)
class App:
    name: str
    dir: Path
    config: dict

    def __str__(self) -> str:
        return self.name


def discover_services() -> list[Service]:
    out = []
    for cfg in sorted(INFRA.glob("*/service.yaml")):
        out.append(
            Service(name=cfg.parent.name, dir=cfg.parent, config=yaml.safe_load(cfg.read_text()))
        )
    return out


def discover_apps() -> list[App]:
    out = []
    for cfg in sorted(APPS.glob("*/app.yaml")):
        out.append(
            App(name=cfg.parent.name, dir=cfg.parent, config=yaml.safe_load(cfg.read_text()))
        )
    return out


def pytest_generate_tests(metafunc):
    if "service" in metafunc.fixturenames:
        services = discover_services()
        metafunc.parametrize("service", services, ids=[s.name for s in services])
    if "app" in metafunc.fixturenames:
        apps = discover_apps()
        metafunc.parametrize("app", apps, ids=[a.name for a in apps])


def have(binary: str) -> bool:
    return (
        subprocess.run(["which", binary], capture_output=True, text=True).returncode == 0
    )


needs_helm = pytest.mark.skipif(not have("helm"), reason="helm not installed")
needs_docker = pytest.mark.skipif(not have("docker"), reason="docker not installed")
needs_kubectl = pytest.mark.skipif(not have("kubectl"), reason="kubectl not installed")
