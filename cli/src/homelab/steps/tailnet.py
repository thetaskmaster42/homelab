"""Tailnet device cleanup on teardown.

The Tailscale operator registers one tailnet device per exposed Ingress or
Service. `homelab nuke` destroys the pods behind them abruptly, so those devices
are never logged out — they simply stop answering.

They are ephemeral, so Tailscale reaps them on its own eventually. The problem is
the gap. Rebuild inside that window and the operator finds `argocd` still taken,
so the new device registers as `argocd-1`, then `argocd-2`, and every URL in the
docs, every bookmark and every `appUrl` in a values file quietly points at a
hostname that no longer resolves to anything.

Deleting them explicitly at teardown closes the race rather than waiting it out.

Only devices carrying `tag:k8s` or `tag:k8s-operator` are touched. Those tags are
applied exclusively by the operator's OAuth client, so nothing a human enrolled
can match — the laptop, phones and any other tailnet member are untagged or
tagged otherwise, and are never candidates.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

import yaml

from ..errors import HomelabError

OAUTH_URL = "https://api.tailscale.com/api/v2/oauth/token"
DEVICES_URL = "https://api.tailscale.com/api/v2/tailnet/-/devices"
DEVICE_URL = "https://api.tailscale.com/api/v2/device/{id}"

# Applied only by the operator's OAuth client. See docs/tailscale.md.
CLUSTER_TAGS = frozenset({"tag:k8s", "tag:k8s-operator"})


def oauth_from_bundle(plaintext: str) -> tuple[str, str] | None:
    """Pull the operator's OAuth client out of the decrypted bootstrap bundle.

    Returns None rather than raising when it is absent: a cluster that never had
    Tailscale configured should still be nukeable.
    """
    for doc in yaml.safe_load_all(plaintext):
        if not doc or doc.get("kind") != "Secret":
            continue
        if doc.get("metadata", {}).get("name") != "operator-oauth":
            continue
        data = doc.get("stringData") or {}
        cid, secret = data.get("client_id"), data.get("client_secret")
        if cid and secret:
            return cid, secret
    return None


def access_token(client_id: str, client_secret: str, *, timeout: int = 30) -> str:
    body = urllib.parse.urlencode(
        {"client_id": client_id, "client_secret": client_secret}
    ).encode()
    try:
        with urllib.request.urlopen(OAUTH_URL, body, timeout=timeout) as r:
            return json.load(r)["access_token"]
    except urllib.error.HTTPError as exc:
        raise HomelabError(
            f"Tailscale OAuth rejected the operator client ({exc.code}). "
            "The tailnet devices will have to be removed by hand, or left to "
            "expire on their own."
        ) from exc


def _get(url: str, token: str, timeout: int = 30):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def cluster_devices(token: str) -> list[dict]:
    """Devices this cluster created, newest-looking name first for stable output."""
    devices = _get(DEVICES_URL, token).get("devices", [])
    out = [d for d in devices if CLUSTER_TAGS & set(d.get("tags") or ())]
    return sorted(out, key=lambda d: d.get("hostname", ""))


def delete_device(token: str, device_id: str, *, timeout: int = 30) -> None:
    req = urllib.request.Request(
        DEVICE_URL.format(id=device_id),
        headers={"Authorization": f"Bearer {token}"},
        method="DELETE",
    )
    with urllib.request.urlopen(req, timeout=timeout):
        return


def suffixed(devices: list[dict]) -> list[str]:
    """Hostnames that look like collision artefacts: `argocd-1`, `grafana-2`.

    Reported rather than acted on. A name genuinely ending in a digit is
    possible, and guessing wrong here would delete the wrong device.
    """
    import re

    return [
        d["hostname"]
        for d in devices
        if d.get("hostname") and re.search(r"-\d+$", d["hostname"])
    ]
