
#!/bin/bash

nuke=$1
echo "Preparing to nuke all $nuke resources..."

echo "Nuking all $nuke resources in the cluster..."

case $nuke in
    kestra)
    cd /home/rudra/Documents/Workbench/projects/data-engineering-zoomcamp/02-workflow-orchestration
    docker compose down
    echo "Kestra resources nuked."
    exit 0
    ;;
    monitoring)
    echo "Nuking all Monitoring resources in the cluster..."
    kubectl delete namespace monitoring
    echo "Monitoring resources nuked."
    exit 0
    ;;
    k3s)
    echo "Uninstalling k3s from all nodes..."
    # master node
    sudo /usr/local/bin/k3s-uninstall.sh
    # worker nodes
    ssh rudra@pi1 "sudo /usr/local/bin/k3s-agent-uninstall.sh"
    ssh rudra@pi2 "sudo /usr/local/bin/k3s-agent-uninstall.sh"
    ssh rudra@pi3 "sudo /usr/local/bin/k3s-agent-uninstall.sh"
    ssh rudra@pi4 "sudo /usr/local/bin/k3s-agent-uninstall.sh"
    echo "k3s uninstalled from all nodes."
    exit 0
    ;;
    *)
    echo "Unknown resource type: $nuke. Please specify 'kestra', 'monitoring', or 'k3s'."
    exit 1
    ;;
esac
