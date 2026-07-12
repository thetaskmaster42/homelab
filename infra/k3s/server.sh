#!/bin/bash
set -euo pipefail

# Install the k3s control plane. Run ON the control-plane node
# (the Proxmox CT/VM, e.g. k3s-master).
#
# Usage: ./server.sh

fqdn="$(hostname -s).rps-home.com"
node_ip="$(dig +short "$fqdn")"

if [ -z "$node_ip" ]; then
    echo "ERROR: $fqdn does not resolve — add the A record in Pi-hole first." >&2
    exit 1
fi

curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC="server \
    --tls-san $fqdn \
    --node-ip $node_ip \
    --write-kubeconfig-mode 644" sh -s -

echo
echo "k3s server installed. Join token for agent.sh:"
sudo cat /var/lib/rancher/k3s/server/node-token
