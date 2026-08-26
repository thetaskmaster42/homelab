#!/bin/bash
# Recover k3s-server from the link-up-but-no-traffic blackhole.
#
# The failure mode this exists for: eth0 reports 1Gbps/Full with zero error
# counters, the kernel is healthy and logging, but no packet moves in either
# direction. The node stays that way until someone power-cycles it — 402 minutes
# in the worst observed case.
#
# The hardware watchdog does NOT catch this. bcm2835-wdt only fires when the
# kernel stops petting it, and here the kernel is fine; it is the network path
# that is dead. So this checks reachability instead of liveness.
#
# Escalation: bounce the interface (proves whether the fault is driver/PHY-side
# or external), then reboot. Each step is recorded so the next occurrence
# produces evidence rather than another mystery.
set -euo pipefail
cd "$(dirname "$0")"

PROBE_IPS="${PROBE_IPS:-192.168.11.1 192.168.11.3}"
IFACE="${IFACE:-eth0}"
FAIL_THRESHOLD="${FAIL_THRESHOLD:-6}"     # consecutive failures (x INTERVAL) before acting
INTERVAL="${INTERVAL:-10}"
LOG=/var/log/net-watchdog.log

log() { echo "$(date '+%F %T') $*" | tee -a "$LOG" >&2; }

reachable() {
  for ip in $PROBE_IPS; do
    ping -c1 -W2 -I "$IFACE" "$ip" >/dev/null 2>&1 && return 0
  done
  return 1
}

# Snapshot the state that distinguishes a driver wedge from a cable/switch
# fault. Counters are per-boot, so they must be captured BEFORE the reboot.
evidence() {
  log "--- evidence ---"
  ip -s link show "$IFACE" 2>&1 | tee -a "$LOG" >&2 || true
  ethtool "$IFACE" 2>&1 | grep -iE "speed|duplex|link detected" | tee -a "$LOG" >&2 || true
  ethtool -S "$IFACE" 2>&1 | grep -viE ": 0$" | tee -a "$LOG" >&2 || true
  ip neigh show | tee -a "$LOG" >&2 || true
}

fails=0
while true; do
  if reachable; then
    [ "$fails" -gt 0 ] && log "recovered after $fails failed probes"
    fails=0
  else
    fails=$((fails + 1))
    log "probe failed ($fails/$FAIL_THRESHOLD)"

    if [ "$fails" -eq "$FAIL_THRESHOLD" ]; then
      evidence
      log "bouncing $IFACE"
      ip link set "$IFACE" down; sleep 3; ip link set "$IFACE" up; sleep 15
      if reachable; then
        # This is the diagnostic payoff: a link bounce fixing it means the
        # fault is on the Pi's side (driver/PHY), not the cable or switch.
        log "RECOVERED BY LINK BOUNCE -> fault is driver/PHY-side, not external"
        fails=0
      fi
    elif [ "$fails" -ge $((FAIL_THRESHOLD * 2)) ]; then
      log "still dead after link bounce -> rebooting"
      systemctl reboot
      exit 0
    fi
  fi
  sleep "$INTERVAL"
done
