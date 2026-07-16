#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

# Install the k3s control plane. Run ON the control-plane node
# (the Proxmox CT/VM, e.g. k3s-master).
#
# Usage: ./server.sh
#
# Deviations from k3s defaults:
#   - flannel disabled       -> Calico (installed below) is the CNI
#   - network policy disabled -> Calico enforces policy instead
#   - servicelb disabled     -> MetalLB (via ArgoCD) provides LoadBalancer IPs

# TODO: bump to the latest Calico release when reprovisioning
CALICO_VERSION="v3.29.1"

fqdn="$(hostname -s).rps-home.com"
node_ip="$(dig +short "$fqdn")"

if [ -z "$node_ip" ]; then
    echo "ERROR: $fqdn does not resolve — add the A record in Pi-hole first." >&2
    exit 1
fi

curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC="server \
    --flannel-backend=none \
    --disable-network-policy \
    --disable=servicelb \
    --tls-san $fqdn \
    --node-ip $node_ip \
    --write-kubeconfig-mode 644" sh -s -

export KUBECONFIG=/etc/rancher/k3s/k3s.yaml

# Calico must be installed here, not via ArgoCD: without a CNI no pod
# (including ArgoCD itself) can start.
echo "Installing Calico $CALICO_VERSION..."
kubectl create -f "https://raw.githubusercontent.com/projectcalico/calico/$CALICO_VERSION/manifests/tigera-operator.yaml"
until kubectl apply -f calico-installation.yaml; do
    echo "Waiting for the Tigera operator CRDs..."
    sleep 5
done

echo "Waiting for the node to become Ready (Calico coming up)..."
kubectl wait --for=condition=Ready node --all --timeout=300s

echo
echo "k3s server installed. Join token for k3s-worker-agent.sh:"
sudo cat /var/lib/rancher/k3s/server/node-token
