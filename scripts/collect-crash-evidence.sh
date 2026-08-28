#!/bin/bash
# Wait for k3s-server to come back, then capture why it died.
#
# The evidence lives on the box that crashed, and the box is unreachable while
# it is crashed — so this runs on the laptop, waits for it, and grabs the
# previous boot's journal before anything overwrites it. journald is persistent
# on these nodes, so `-b -1` survives the power cut that caused it.
set -euo pipefail
cd "$(dirname "$0")"

HOST="${1:-192.168.11.7}"
USER_="${SSH_USER:-rudra}"
OUT="${OUT_DIR:-/tmp/homelab-crash}"
DEADLINE=$(( $(date +%s) + ${WAIT_SECONDS:-86400} ))

mkdir -p "$OUT"
echo "waiting for $HOST (deadline $(date -d "@$DEADLINE" '+%F %T'))..."

until ping -c1 -W2 "$HOST" >/dev/null 2>&1; do
  [ "$(date +%s)" -lt "$DEADLINE" ] || { echo "gave up waiting for $HOST"; exit 1; }
  sleep 20
done

echo "$HOST answered at $(date '+%F %T'); collecting..."
# Give sshd a moment; ping answers before the userland is up.
until ssh -o ConnectTimeout=5 -o BatchMode=yes "$USER_@$HOST" true 2>/dev/null; do sleep 5; done

STAMP=$(date '+%Y%m%d-%H%M%S')
REPORT="$OUT/crash-$STAMP.txt"

ssh -o BatchMode=yes "$USER_@$HOST" 'bash -s' > "$REPORT" <<'REMOTE'
echo "===== collected $(date '+%F %T') on $(hostname) ====="
echo "--- uptime / model / boot media ---"
uptime; tr -d '\0' < /proc/device-tree/model; echo; findmnt -n -o SOURCE,FSTYPE /

echo "--- PSU: negotiated current (Pi 5 wants 5000 mA) ---"
python3 -c "import struct;print(struct.unpack('>I',open('/sys/firmware/devicetree/base/chosen/power/max_current','rb').read())[0],'mA')" 2>/dev/null || echo n/a

echo "--- undervoltage alarms (rpi_volt) ---"
for f in /sys/class/hwmon/hwmon*/in0_lcrit_alarm; do echo "$f = $(cat $f 2>/dev/null)"; done

echo "--- boots retained ---"
# READ THE SECOND DATE, NOT THE FIRST. A Pi 5 has no RTC battery, so every boot
# starts with the same bogus clock until NTP corrects it -- every row here shows
# an identical, meaningless start time. The END timestamp is the real signal:
# it is when that boot stopped, i.e. when the node went down.
sudo journalctl --list-boots --no-pager | tail -6

echo "--- did the PREVIOUS boot end cleanly? ---"
sudo journalctl -b -1 --no-pager | tail -40

echo "--- previous boot: undervoltage / thermal / panic / OOM ---"
sudo journalctl -b -1 -k --no-pager \
  | grep -iE "under.?voltage|throttl|thermal|kernel panic|Oops|BUG:|hung task|soft lockup|Out of memory|oom-kill|mmc|I/O error|EXT4-fs error" | tail -40

echo "--- this boot: filesystem recovery (proof of an unclean stop) ---"
sudo journalctl -b 0 -k --no-pager | grep -iE "recovering journal|EXT4-fs .*(recovery|error)|orphan" | head -10

echo "--- previous boot: network reachability failures ---"
# The signature that distinguishes the two failure modes, and the reason this
# section exists: a node whose NETWORK died stays alive and logs these for as
# long as it is isolated, while a node that actually died logs nothing at all
# and its journal simply stops mid-line. On k3s-worker-1 this showed the node
# was up and retrying for 2h10m AFTER Kubernetes had written it off.
#
# Both the control plane and the NAS appear here, deliberately: if only the API
# server is unreachable it is a k3s problem, but if the NAS is unreachable too
# then the whole network path is gone.
sudo journalctl -b -1 --no-pager \
  | grep -iE "not responding|connection timed out|Failed to connect to proxy|no route to host|network is unreachable|NETDEV WATCHDOG|link is not ready|carrier lost" \
  | tail -25

echo "--- k3s: last words before it died ---"
# The unit name differs by role -- `k3s` on the server, `k3s-agent` on workers.
# Asking for the wrong one returns "No entries", which reads as "the node said
# nothing" rather than "you asked the wrong question". That false negative cost
# a whole section of the k3s-worker-1 post-mortem, so resolve it rather than
# assume.
k3s_unit=""
for u in k3s k3s-agent; do
  if systemctl cat "$u.service" >/dev/null 2>&1; then k3s_unit="$u"; break; fi
done
if [ -n "$k3s_unit" ]; then
  echo "(unit: $k3s_unit)"
  sudo journalctl -u "$k3s_unit" -b -1 --no-pager | tail -30
else
  echo "no k3s or k3s-agent unit found on this host"
fi

echo "--- current link state ---"
# Baseline only, and worth being explicit about why: interface counters and link
# status are per-boot and were reset by the reboot that made this host reachable
# again. The failure's own counters are gone by the time this script can run --
# capturing those is what scripts/net-watchdog.sh is for, before it reboots.
ip -s link show eth0 2>/dev/null | head -6
ethtool eth0 2>/dev/null | grep -iE "speed|duplex|link detected"
REMOTE

echo "written: $REPORT"
