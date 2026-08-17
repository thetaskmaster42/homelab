"""Config validation.

Each case here is a mistake that would otherwise be discovered over SSH, on a
Raspberry Pi, partway through an install.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from homelab.config import Cluster
from homelab.errors import ConfigError


def test_real_config_is_valid(cluster):
    assert cluster.name == "rps"
    assert cluster.server.name == "k3s-server"
    assert [n.name for n in cluster.agents] == ["k3s-worker-1", "k3s-worker-2"]


def test_two_servers_rejected(cfg):
    cfg["spec"]["nodes"][1]["role"] = "server"
    with pytest.raises(ValidationError, match="exactly one node must have role: server"):
        Cluster.model_validate(cfg)


def test_no_server_rejected(cfg):
    for node in cfg["spec"]["nodes"]:
        node["role"] = "agent"
    with pytest.raises(ValidationError, match="exactly one node"):
        Cluster.model_validate(cfg)


def test_duplicate_ip_rejected(cfg):
    cfg["spec"]["nodes"][1]["ip"] = cfg["spec"]["nodes"][0]["ip"]
    with pytest.raises(ValidationError, match="duplicate node ip"):
        Cluster.model_validate(cfg)


def test_duplicate_name_rejected(cfg):
    cfg["spec"]["nodes"][1]["name"] = cfg["spec"]["nodes"][0]["name"]
    with pytest.raises(ValidationError, match="duplicate node name"):
        Cluster.model_validate(cfg)


@pytest.mark.parametrize("bad", ["v1.36.3", "latest", "1.36.3+k3s1", "stable"])
def test_unpinned_k3s_version_rejected(cfg, bad):
    cfg["spec"]["k3s"]["version"] = bad
    with pytest.raises(ValidationError, match="fully pinned"):
        Cluster.model_validate(cfg)


def test_pod_cidr_containing_a_node_rejected(cfg):
    """A pod CIDR that swallows a node IP blackholes that node the instant the
    CNI comes up — possibly the one you are SSH'd into."""
    cfg["spec"]["k3s"]["clusterCIDR"] = "192.168.11.0/24"
    with pytest.raises(ValidationError, match="contains node"):
        Cluster.model_validate(cfg)


def test_overlapping_cluster_and_service_cidrs_rejected(cfg):
    cfg["spec"]["k3s"]["serviceCIDR"] = "10.42.0.0/16"
    with pytest.raises(ValidationError, match="overlaps"):
        Cluster.model_validate(cfg)


def test_ingress_ip_outside_metallb_pool_rejected(cfg):
    """Traefik pins ingressIP; outside the pool the Service stays <pending>
    forever with nothing explaining why."""
    cfg["spec"]["network"]["ingressIP"] = "192.168.11.99"
    with pytest.raises(ValidationError, match="outside"):
        Cluster.model_validate(cfg)


def test_reversed_pool_rejected(cfg):
    cfg["spec"]["network"]["loadBalancerPool"] = "192.168.11.250-192.168.11.200"
    with pytest.raises(ValidationError, match="start is after its end"):
        Cluster.model_validate(cfg)


def test_unknown_key_rejected(cfg):
    """A typo'd key silently ignored means the setting you thought you changed
    never took effect."""
    cfg["spec"]["k3s"]["verison"] = "v1.36.3+k3s1"
    with pytest.raises(ValidationError):
        Cluster.model_validate(cfg)


def test_unknown_node_lookup_lists_known_names(cluster):
    with pytest.raises(ConfigError, match="k3s-server"):
        cluster.node("nope")


def test_missing_file_is_a_clean_error(tmp_path):
    from homelab import config as config_mod

    with pytest.raises(ConfigError, match="no cluster config"):
        config_mod.load(tmp_path / "absent.yaml")


def test_malformed_yaml_is_a_clean_error(tmp_path):
    from homelab import config as config_mod

    p = tmp_path / "cluster.yaml"
    p.write_text("spec: [unclosed\n")
    with pytest.raises(ConfigError, match="not valid YAML"):
        config_mod.load(p)
