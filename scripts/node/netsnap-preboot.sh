#!/bin/bash
# Preserve the snapshots from the boot that just ended, before anything overwrites
# them.
#
# This exists because of a specific loss. worker-1 blackholed for 14.5 hours with
# the sentinel's predecessor running; it was rebooted; and both snapshots were
# overwritten within two minutes by the timer restarting. The one dataset the
# whole exercise was built to produce was destroyed by the recovery.
#
# So: on every boot, BEFORE any other netsnap unit runs, move whatever survived
# into preboot/ and keep two generations. The newest pre-boot snapshot is almost
# always the most valuable file on the node -- it is the last thing the machine
# saw before it stopped being reachable.
set -euo pipefail

DIR=/var/log/netsnap
mkdir -p "$DIR/preboot"

# Two generations: the boot that just ended, and the one before it. Anything
# older is superseded by the archive, which keeps a day.
if [ -f "$DIR/preboot/preboot-1.txt" ]; then
  mv -f "$DIR/preboot/preboot-1.txt" "$DIR/preboot/preboot-2.txt"
fi

# Prefer the last-good snapshot; fall back to whatever the old layout left.
for candidate in "$DIR/last-good.txt" "$DIR/netsnap.txt" "$DIR/netsnap_old.txt"; do
  if [ -f "$candidate" ]; then
    mv -f "$candidate" "$DIR/preboot/preboot-1.txt"
    echo "preserved $candidate as preboot/preboot-1.txt"
    break
  fi
done

# Failure captures are already timestamped and are never rotated away, so they
# need no preservation -- only a note that they are there to be read.
n=$(find "$DIR/failure" -name '*.txt' 2>/dev/null | wc -l)
if [ "$n" -gt 0 ]; then
  echo "failure/ holds $n captured snapshot(s) from previous incidents"
fi
exit 0
