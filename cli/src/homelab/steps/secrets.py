"""Bootstrap secrets: the credentials that must exist before ArgoCD can work.

These are the chain-of-trust root. One age private key on your laptop unlocks a
public repository full of encrypted values — and that key cannot itself live in
the repo it unlocks, so the CLI installs it. Everything downstream follows from
that one manual step.

Deliberately narrow. A secret belongs here only if the platform cannot reach a
working state without it. Anything an application reads at runtime belongs in
OpenBao instead, which starts sealed and so can never be on this path. See
docs/decisions/0004-two-tier-secrets.md.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from ..errors import HomelabError
from ..runner import Runner

# Where sops looks for the age identity by default.
AGE_KEY_PATH = Path.home() / ".config" / "sops" / "age" / "keys.txt"

# ArgoCD's repo-server reads this to decrypt *.enc.yaml in the repo.
AGE_SECRET_NAME = "sops-age"
AGE_SECRET_NAMESPACE = "argocd"
AGE_SECRET_KEY = "keys.txt"


def bootstrap_file(repo_root: Path, cluster_name: str) -> Path:
    return repo_root / "clusters" / cluster_name / "bootstrap-secrets.enc.yaml"


def example_file(repo_root: Path, cluster_name: str) -> Path:
    return repo_root / "clusters" / cluster_name / "bootstrap-secrets.example.yaml"


def sops_available() -> bool:
    return shutil.which("sops") is not None


def decrypt(runner: Runner, path: Path) -> str:
    """Decrypt to memory. The plaintext is never written to disk — writing it,
    even to a temp file, is how it ends up committed."""
    if not sops_available():
        raise HomelabError(
            "sops is not installed. Install it from "
            "https://github.com/getsops/sops/releases (arm64 and amd64 builds), "
            "then re-run."
        )
    if not AGE_KEY_PATH.is_file():
        raise HomelabError(
            f"no age key at {AGE_KEY_PATH}. Generate one with:\n"
            f"  mkdir -p {AGE_KEY_PATH.parent} && age-keygen -o {AGE_KEY_PATH}\n"
            f"then put its public key in .sops.yaml and re-encrypt."
        )
    result = runner.run(["sops", "--decrypt", str(path)], mutates=False, timeout=120)
    if not result.stdout.strip():
        raise HomelabError(f"decrypting {path} produced nothing")
    return result.stdout


def install_age_key(runner: Runner) -> None:
    """Put the age private key in the cluster so ArgoCD can decrypt the repo.

    This is the one genuine chicken-and-egg in the whole design: the key that
    decrypts the repository cannot be stored in the repository.
    """
    if not AGE_KEY_PATH.is_file():
        raise HomelabError(f"no age key at {AGE_KEY_PATH}")

    runner.run(
        ["kubectl", "create", "namespace", AGE_SECRET_NAMESPACE],
        check=False,
    )
    # create --dry-run | apply so re-running bootstrap converges rather than
    # failing on "already exists" — bootstrap doubles as break-glass recovery.
    runner.run(
        [
            "bash", "-c",
            f"kubectl create secret generic {AGE_SECRET_NAME} "
            f"--namespace {AGE_SECRET_NAMESPACE} "
            f"--from-file={AGE_SECRET_KEY}={AGE_KEY_PATH} "
            f"--dry-run=client -o yaml | kubectl apply -f -",
        ],
        timeout=120,
    )


def apply_bootstrap_secrets(runner: Runner, repo_root: Path, cluster_name: str) -> int:
    """Apply the encrypted bundle. Returns how many Secrets were applied."""
    path = bootstrap_file(repo_root, cluster_name)
    if not path.is_file():
        return 0

    plaintext = decrypt(runner, path)

    # Namespaces are created first: the Secrets target namespaces that belong to
    # services ArgoCD has not necessarily synced yet, and a Secret cannot be
    # applied into a namespace that does not exist.
    import yaml

    namespaces = {
        doc["metadata"]["namespace"]
        for doc in yaml.safe_load_all(plaintext)
        if doc and doc.get("metadata", {}).get("namespace")
    }
    for ns in sorted(namespaces):
        runner.run(["kubectl", "create", "namespace", ns], check=False)

    runner.run(
        ["bash", "-c", "kubectl apply -f - <<'HOMELAB_EOF'\n" + plaintext + "\nHOMELAB_EOF"],
        timeout=120,
    )
    return sum(1 for doc in yaml.safe_load_all(plaintext) if doc and doc.get("kind") == "Secret")
