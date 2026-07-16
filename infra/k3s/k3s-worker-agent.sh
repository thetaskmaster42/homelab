#!/bin/bash
set -euo pipefail

# Join this machine to the cluster as a worker. Run ON each worker node
# (node-1, node-2, Proxmox CTs, later shield).
#
# Usage: K3S_TOKEN=<token> ./k3s-worker-agent.sh [server-fqdn]
#   token:       /var/lib/rancher/k3s/server/node-token on the server
#   server-fqdn: defaults to k3s-master.rps-home.com

server="${1:-k3s-master.rps-home.com}"
: "${K3S_TOKEN:?Set K3S_TOKEN (from /var/lib/rancher/k3s/server/node-token on the server)}"

curl -sfL https://get.k3s.io | \
    K3S_URL="https://${server}:6443" \
    K3S_TOKEN="$K3S_TOKEN" \
    K3S_NODE_NAME="$(hostname -s)" \
    sh -s -
