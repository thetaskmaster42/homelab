#!/usr/bin/env bash
# Network state snapshot, run on every node by netsnap.timer.
#
# Adopted verbatim from the operator's own script; the section list is the
# useful part and is left as written. It is in the repo so `homelab install`
# deploys it to every node, including ones rebuilt later — a diagnostic that
# only exists on the node you happened to copy it to is not a diagnostic.
#
# What it is FOR: the nodes intermittently stop passing traffic while the link
# still reports 1Gbps/Full with zero error counters, and recover only on reboot.
# A reboot resets every counter, so the state has to be captured while it is
# still broken. See docs/decisions/0012-node-network-blackholes.md.
# Network state snapshot. Run on every node; diff the outputs.
# usage: sudo netsnap.sh > /tmp/snap-$(hostname).txt
IF="${1:-eth0}"
s(){ printf '\n===== %s =====\n' "$1"; }

s MODEL;        tr -d '\0' < /proc/device-tree/model; echo; uname -a
s KERNEL_PKGS;  dpkg -l | awk '/linux-(image|modules|raspi|firmware)/{print $2, $3}'
s EEPROM;       rpi-eeprom-update 2>/dev/null; vcgencmd bootloader_version 2>/dev/null
s THROTTLE;     vcgencmd get_throttled 2>/dev/null; vcgencmd measure_temp 2>/dev/null
s GOVERNOR;     cat /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor 2>/dev/null
s ADDR;         ip -br addr
s LINK;         ip -br link; ip -d link show "$IF" | head -8
s ROUTE;        ip route show table all
s RULES;        ip rule show
s NEIGH;        ip neigh show
s DRIVER;       ethtool -i "$IF"
s LINKSTAT;     ethtool "$IF"
s EEE;          ethtool --show-eee "$IF" 2>&1
s RING;         ethtool -g "$IF" 2>&1
s COALESCE;     ethtool -c "$IF" 2>&1
s OFFLOAD;      ethtool -k "$IF"
s PAUSE;        ethtool -a "$IF" 2>&1
s STATS_NONZERO; ethtool -S "$IF" | grep -vE ': 0$'
s SOFTNET;      cat /proc/net/softnet_stat
s IRQ;          grep -iE 'eth|macb|rp1|xhci' /proc/interrupts
s SYSCTL;       sysctl -a --pattern '^net\.(ipv4\.(conf\.(all|'"$IF"')|ip_forward|tcp_retries|neigh)|ipv6\.conf\.(all|'"$IF"')|core|netfilter)' 2>/dev/null | sort
s CONNTRACK;    cat /proc/sys/net/netfilter/nf_conntrack_count /proc/sys/net/netfilter/nf_conntrack_max 2>/dev/null
s NETPLAN;      find /etc/netplan -type f -print -exec cat {} \;
s NETPLAN_EFF;  netplan get all 2>/dev/null
s RENDERER;     systemctl is-active systemd-networkd NetworkManager systemd-resolved
s NETWORKCTL;   networkctl status "$IF" 2>/dev/null
s CLOUDINIT;    ls /etc/cloud/cloud.cfg.d/ 2>/dev/null; cat /etc/cloud/cloud.cfg.d/*network* 2>/dev/null
s LEASE;        cat /run/systemd/netif/leases/* 2>/dev/null
s DNS;          resolvectl status 2>/dev/null | head -30
s LISTEN;       ss -tulpn | sort
s FW_BACKEND;   iptables -V; update-alternatives --display iptables 2>/dev/null | head -3
s NFT_COUNT;    nft list ruleset 2>/dev/null | wc -l
s LEGACY_COUNT; iptables-legacy-save 2>/dev/null | wc -l
s MODULES;      lsmod | awk '{print $1}' | sort
s PCI_USB;      lspci -nn 2>/dev/null; lsusb 2>/dev/null
s KUBELET;      systemctl cat kubelet 2>/dev/null | grep -iE 'node-ip|ExecStart'
s NET_ERRORS;   journalctl -k --since -7d --no-pager \
                 | grep -iE 'link is|carrier|macb|rp1|conntrack|hung task|rcu|watchdog|under-voltage' \
                 | tail -60
