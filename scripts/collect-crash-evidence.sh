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
sudo journalctl --list-boots --no-pager | tail -6

echo "--- did the PREVIOUS boot end cleanly? ---"
sudo journalctl -b -1 --no-pager | tail -40

echo "--- previous boot: undervoltage / thermal / panic / OOM ---"
sudo journalctl -b -1 -k --no-pager \
  | grep -iE "under.?voltage|throttl|thermal|kernel panic|Oops|BUG:|hung task|soft lockup|Out of memory|oom-kill|mmc|I/O error|EXT4-fs error" | tail -40

echo "--- this boot: filesystem recovery (proof of an unclean stop) ---"
sudo journalctl -b 0 -k --no-pager | grep -iE "recovering journal|EXT4-fs .*(recovery|error)|orphan" | head -10

echo "--- k3s server: last words before it died ---"
sudo journalctl -u k3s -b -1 --no-pager | tail -30
REMOTE

echo "written: $REPORT"
