#!/bin/bash

resource=$1
echo "Starting to set up $resource resources..."

case $resource in
    kestra)
    cd /home/rudra/Documents/Workbench/projects/data-engineering-zoomcamp/02-workflow-orchestration
    docker compose up -d
    echo "Kestra resources set up."
    exit 0
    ;;
    monitoring)
    echo "Setting up Monitoring resources in the cluster..."
    cd /home/rudra/Documents/Workbench/projects/dataengineering/01-infra/homelab/monitoring
    sh install.sh
    echo "Monitoring resources set up."
    exit 0
    ;;
    k3s)
    echo "Setting up k3s cluster..."
    ./k3s_install.sh
    echo "k3s cluster set up."
    exit 0
    ;;
    *)
    echo "Unknown resource type: $resource. Please specify 'kestra', 'monitoring', or 'k3s'."
    exit 1
    ;;
esac