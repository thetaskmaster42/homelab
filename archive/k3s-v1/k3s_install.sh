#!/bin/bash

# Master setup

master_node_ip=`dig +short server.lan`

sudo curl -sfL https://get.k3s.io | \
K3S_KUBECONFIG_MODE="777" \
INSTALL_K3S_EXEC="server \
--flannel-backend=host-gw \
--tls-san=$master_node_ip \
--bind-address=$master_node_ip \
--advertise-address=$master_node_ip \
--node-ip=$master_node_ip \
--cluster-init" sh -s -

node_token=$(sudo cat /var/lib/rancher/k3s/server/node-token)

# workers setup 

for node in "node-1.lan" "node-2.lan" "node-3.lan"; do
    ip=`dig +short $node`
    name=$(echo $node | cut -d'.' -f1)
    echo"sudo curl -sfL https://get.k3s.io | \
    K3S_TOKEN=$node_token \
    K3S_URL='https://$master_node_ip:6443' \
    K3S_NODE_NAME='$name' sh - "
    echo '------------------------------'
done
