"""No YAML file may define the same key twice at the same level.

PyYAML — and Helm — silently keep the last occurrence. A second `server:` block
added to argocd/bootstrap/values.yaml discarded an entire ingress definition with
no error anywhere: the file parsed, the chart rendered, and the Ingress simply did
not exist. Nothing in yamllint's default rules catches it either.

This is the worst shape a config bug can take, because every tool reports success.
"""

from __future__ import annotations

import subprocess

import pytest
import yaml

from conftest import REPO


class DuplicateKeyLoader(yaml.SafeLoader):
    """SafeLoader that refuses duplicate mapping keys instead of silently
    keeping the last one."""


def _no_duplicates(loader, node, deep=False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                None, None,
                f"duplicate key {key!r} at line {key_node.start_mark.line + 1}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


DuplicateKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_duplicates
)


def yaml_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "*.yaml", "*.yml"],
        cwd=REPO, capture_output=True, text=True,
    )
    return sorted(
        f for f in out.stdout.splitlines()
        # sops output is machine-generated and its own concern.
        if f and not f.endswith(".enc.yaml")
    )


@pytest.mark.parametrize("relpath", yaml_files())
def test_no_duplicate_keys(relpath):
    text = (REPO / relpath).read_text()
    try:
        list(yaml.load_all(text, Loader=DuplicateKeyLoader))
    except yaml.constructor.ConstructorError as exc:
        pytest.fail(
            f"{relpath}: {exc.problem}\n"
            f"The later value silently wins, so whatever the first block "
            f"configured is discarded with no error from Helm or yamllint."
        )
    except yaml.YAMLError:
        pytest.skip(f"{relpath} is not plain YAML (template or non-standard tags)")
