#!/bin/bash

# Master setup

master_node_ip=""

/usr/local/bin/k3s-uninstall.sh


# workers setup 

pi1 "/user/local/bin/k3s-uninstall.sh"


pi2 "/user/local/bin/k3s-uninstall.sh"

pi3 "/user/local/bin/k3s-uninstall.sh"

pi4 "/user/local/bin/k3s-uninstall.sh"