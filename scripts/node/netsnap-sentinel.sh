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
# Recovery is STAGED rather than going straight to a link bounce. A bounce
# resets three things at once -- the Pi's MAC/PHY, the ARP cache (because
# arp_evict_nocarrier=1), and the switch's view of the port -- so "a bounce
# fixed it" cannot say which of the three was stuck. Escalating in order of
# blast radius, and recording which step restored traffic, decomposes that.
NEIGH_FLUSH_AFTER="${NEIGH_FLUSH_AFTER:-9}"  # kernel neighbour state only (~90s)
RENEG_AFTER="${RENEG_AFTER:-12}"      # PHY autoneg restart, no admin down (~2m)
BOUNCE_AFTER="${BOUNCE_AFTER:-15}"    # full MAC+PHY reset, switch re-learn (~2.5m)
COUNTER_WINDOW="${COUNTER_WINDOW:-10}" # seconds between the two in-failure samples
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

# Sample the driver counters twice INSIDE the failure window and record the
# delta. This exists because comparing the failure snapshot against last-good
# cannot answer the question that matters: last-good is refreshed every
# GOOD_INTERVAL, so that comparison spans up to five minutes of mostly-healthy
# traffic and its deltas are dominated by normal work. Two samples taken while
# the node is actually isolated are not.
#
# The signal to look for: tx_* climbing while rx_* stays flat means frames are
# leaving and nothing is coming back -- a receive-path fault. Both flat means
# the interface is wedged entirely. Both climbing means the link is carrying
# traffic and the problem is above it.
counter_window() {  # counter_window <destination>
  local dest="$1" a b
  a=$(mktemp "$DIR/.cnt.XXXXXX"); b=$(mktemp "$DIR/.cnt.XXXXXX")
  trap 'rm -f "$a" "$b"' RETURN
  # key:value, digits only -- drops the "NIC statistics:" header and any
  # non-numeric row so the awk join below cannot produce garbage deltas.
  ethtool -S "$IFACE" 2>/dev/null | tr -d ' ' | grep -E '^[a-z0-9_]+:[0-9]+$' > "$a" || true
  sleep "$COUNTER_WINDOW"
  ethtool -S "$IFACE" 2>/dev/null | tr -d ' ' | grep -E '^[a-z0-9_]+:[0-9]+$' > "$b" || true
  {
    echo "===== COUNTER WINDOW: ${COUNTER_WINDOW}s ENTIRELY INSIDE THE FAILURE ====="
    date '+===== SAMPLED %F %T %Z ====='
    echo "# Non-zero deltas only. tx_ climbing + rx_ flat = receive-path fault."
    awk -F: 'NR==FNR{a[$1]=$2;next} ($1 in a) && ($2-a[$1])!=0 {
               printf "%-32s %16s -> %-16s delta %+d\n", $1, a[$1], $2, $2-a[$1] }' "$a" "$b"
    echo "--- counters with zero delta across the window are omitted ---"
    echo "--- if NOTHING is listed, neither rx nor tx advanced at all ---"
  } > "$dest" 2>&1 || true
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
# Which recovery stage was last attempted: "" none, then neigh-flush,
# phy-renegotiate, link-bounce. Reported on recovery so repeated events build a
# record of which layer actually clears the fault.
stage=""

# SIGKILL runs no trap, so sweep whatever a previous hard kill left behind.
find "$DIR" -maxdepth 1 -name '.snap.*' -delete 2>/dev/null || true

log "sentinel started: probing [$PROBE_IPS] every ${INTERVAL}s on $IFACE"

while true; do
  now=$(date +%s)

  if reachable; then
    if [ "$fails" -ge "$FAIL_THRESHOLD" ]; then
      if [ -n "$stage" ]; then
        # "recovered after X" is NOT proof that X fixed it -- the fault may
        # have cleared on its own at that moment. One event says nothing; the
        # same stage clearing it repeatedly, while the cheaper stages before it
        # did not, is what identifies the layer.
        log "RECOVERED AFTER: $stage (after $fails failed probes)"
        case "$stage" in
          neigh-flush)     log "  -> stale kernel neighbour state; NIC and switch were fine" ;;
          phy-renegotiate) log "  -> PHY/link negotiation; a neighbour flush alone did not help" ;;
          link-bounce)     log "  -> needed a full MAC+PHY reset: driver state, or the switch port" ;;
        esac
      else
        log "recovered on its own after $fails failed probes (no action taken)"
      fi
    fi
    fails=0; stage=""

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
      # Deliberately AFTER the full snapshot and BEFORE any recovery attempt:
      # the whole point is a delta measured while the node is still broken and
      # still untouched. Costs COUNTER_WINDOW seconds, which shifts the stages
      # below slightly later in wall-clock but not in probe count.
      counter_window "$DIR/failure/counters-$ts.txt"
      log "captured in-failure counter delta -> failure/counters-$ts.txt"
    fi

    # --- staged recovery, cheapest first -----------------------------------
    # Every stage runs only after the evidence above is on disk, because a link
    # bounce resets the interface counters and would destroy the very numbers
    # that explain the failure.

    if [ "$fails" -eq "$NEIGH_FLUSH_AFTER" ]; then
      # Touches nothing but this kernel's neighbour table: no link event, the
      # switch never notices. If this alone restores traffic, the NIC and the
      # switch were fine and the fault was stale ARP state.
      log "recovery stage 1: flushing neighbour table on $IFACE"
      ip neigh flush dev "$IFACE" 2>/dev/null || true
      stage=neigh-flush
    fi

    if [ "$fails" -eq "$RENEG_AFTER" ]; then
      # Restarts PHY autonegotiation without administratively downing the link.
      # Narrower than a bounce, though not perfectly clean: if carrier drops
      # during renegotiation the switch still sees a link event. It separates
      # "PHY negotiation" from "MAC/driver state", not from the switch entirely.
      log "recovery stage 2: restarting autonegotiation on $IFACE"
      ethtool -r "$IFACE" 2>/dev/null || true
      stage=phy-renegotiate
    fi

    if [ "$fails" -eq "$BOUNCE_AFTER" ]; then
      # The blunt one, kept last. Resets the MAC and PHY, flushes ARP (because
      # arp_evict_nocarrier=1), and makes the switch re-learn the port -- three
      # things at once, which is exactly why the two cheaper stages run first.
      log "recovery stage 3: bouncing $IFACE"
      ip link set "$IFACE" down 2>/dev/null || true
      sleep 3
      ip link set "$IFACE" up 2>/dev/null || true
      stage=link-bounce
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
