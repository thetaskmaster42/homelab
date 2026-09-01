#!/bin/bash
# Watch connectivity; capture evidence the instant it breaks; then try to recover.
#
# This exists because five blackholes produced no usable evidence. The node stays
# alive and logging with `Link detected: yes` and zero error counters, but no ARP
# resolves to anything, and recovery is a reboot -- which resets every counter
# the diagnosis depends on. The state has to be captured while it is still
# broken, on the node, because the node is unreachable at the time.
#
# Ordering is the whole point of merging the probe and the recovery into one
# process. A link bounce RESETS the interface counters, so evidence must be
# captured before any recovery is attempted. Two cooperating daemons would race;
# one cannot.
#
#   healthy            refresh last-good snapshot every GOOD_INTERVAL
#   healthy -> broken  capture failure snapshot + freeze the preceding last-good
#   still broken       escalate: bounce the link, then reboot
#   broken -> healthy  log the recovery, note whether the bounce did it
set -euo pipefail
cd "$(dirname "$0")"

# Overridable so the failure-capture path can be exercised against a scratch
# directory. Verifying capture by cutting real connectivity would mean rebooting
# a node, and writing test artefacts into the real directory would corrupt the
# evidence this whole script exists to preserve.
DIR="${NETSNAP_DIR:-/var/log/netsnap}"
COLLECT=/usr/local/bin/netsnap.sh
IFACE="${IFACE:-eth0}"

# Fixed infrastructure plus peer nodes, rendered by the CLI from cluster.yaml.
PROBE_IPS="${PROBE_IPS:-192.168.11.1 192.168.11.3}"
INTERVAL="${INTERVAL:-10}"            # seconds between probes
FAIL_THRESHOLD="${FAIL_THRESHOLD:-6}" # consecutive failures before "broken" (~60s)
GOOD_INTERVAL="${GOOD_INTERVAL:-300}" # refresh the last-good snapshot every 5m
BOUNCE_AFTER="${BOUNCE_AFTER:-12}"    # failures before bouncing the link (~2m)
REBOOT_AFTER="${REBOOT_AFTER:-90}"    # failures before rebooting (~15m); 0 disables

mkdir -p "$DIR" "$DIR/failure" "$DIR/archive" "$DIR/preboot"

log() { printf '%s %s\n' "$(date '+%F %T')" "$*"; }

snapshot() {  # snapshot <destination>
  local dest="$1" tmp
  tmp=$(mktemp "$DIR/.snap.XXXXXX")
  # Reap the temp file if this snapshot is interrupted. A snapshot is ~50KB and
  # the sentinel is killed on every restart and shutdown, so without this the
  # evidence directory slowly fills with orphaned .snap.* files -- on the one
  # partition whose exhaustion would take the node down.
  trap 'rm -f "$tmp"' RETURN
  # || true: a snapshot missing one section beats no snapshot, and during a
  # blackhole some of these commands are exactly the ones that hang.
  { date '+===== TAKEN %F %T %Z ====='; echo "===== REASON $2 ====="; "$COLLECT"; } \
    > "$tmp" 2>&1 || true
  if [ -s "$tmp" ]; then mv -f "$tmp" "$dest"; else rm -f "$tmp"; fi
}

# Reachable if ANY target answers. All of them failing is what distinguishes
# "this node is isolated" from "one peer is down" -- only the first is our
# problem, and only the first should trigger a reboot.
reachable() {
  local ip
  for ip in $PROBE_IPS; do
    ping -c1 -W2 -I "$IFACE" "$ip" >/dev/null 2>&1 && return 0
  done
  return 1
}

fails=0
last_good_at=0
bounced=0

# SIGKILL runs no trap, so sweep whatever a previous hard kill left behind.
find "$DIR" -maxdepth 1 -name '.snap.*' -delete 2>/dev/null || true

log "sentinel started: probing [$PROBE_IPS] every ${INTERVAL}s on $IFACE"

while true; do
  now=$(date +%s)

  if reachable; then
    if [ "$fails" -ge "$FAIL_THRESHOLD" ]; then
      if [ "$bounced" -eq 1 ]; then
        # The diagnostic payoff. A link bounce fixing it means the fault is on
        # this Pi -- driver or PHY -- and not the cable or the switch.
        log "RECOVERED AFTER LINK BOUNCE -> fault is driver/PHY-side, not external"
      else
        log "recovered on its own after $fails failed probes"
      fi
    fi
    fails=0; bounced=0

    if [ $((now - last_good_at)) -ge "$GOOD_INTERVAL" ]; then
      snapshot "$DIR/last-good.txt" "healthy"
      last_good_at=$now
    fi
  else
    fails=$((fails + 1))
    log "probe failed ($fails)"

    if [ "$fails" -eq "$FAIL_THRESHOLD" ]; then
      ts=$(date '+%Y%m%d-%H%M%S')
      # Freeze the good state FIRST. It is the comparison that makes the failure
      # snapshot readable, and it is the thing a rotation would destroy.
      # if/fi, not `[ ] && cp`: under `set -e` that form exits the daemon when
      # the test is false, and the test is false on the FIRST failure -- before
      # any last-good exists. The sentinel would have died at exactly the moment
      # it is there for.
      if [ -f "$DIR/last-good.txt" ]; then
        cp -f "$DIR/last-good.txt" "$DIR/failure/lastgood-$ts.txt"
      fi
      snapshot "$DIR/failure/failure-$ts.txt" "connectivity lost after $fails probes"
      log "captured failure evidence -> failure/failure-$ts.txt"
    fi

    if [ "$fails" -eq "$BOUNCE_AFTER" ]; then
      # Only now, with evidence already on disk. This resets the counters.
      log "bouncing $IFACE"
      ip link set "$IFACE" down 2>/dev/null || true
      sleep 3
      ip link set "$IFACE" up 2>/dev/null || true
      bounced=1
    fi

    if [ "$REBOOT_AFTER" -gt 0 ] && [ "$fails" -eq "$REBOOT_AFTER" ]; then
      log "still isolated after $fails probes -> rebooting"
      snapshot "$DIR/failure/prereboot-$(date '+%Y%m%d-%H%M%S').txt" "about to reboot"
      sync
      systemctl reboot
      exit 0
    fi
  fi

  sleep "$INTERVAL"
done
