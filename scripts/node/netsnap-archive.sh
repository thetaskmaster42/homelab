#!/bin/bash
# A periodic snapshot, kept for a day.
#
# Between the sentinel's failure captures (the moment it broke) and its last-good
# (five minutes before), a day of half-hourly archives gives the slower context:
# a counter that had been climbing for hours, a neighbour table that had been
# thinning. None of that is visible in two samples a few minutes apart.
#
# 58KB every 30 minutes is 48 files and under 3MB a day -- negligible against
# the earlier once-a-minute design, which wrote 82MB a day and still lost the
# only snapshot that mattered.
set -euo pipefail

DIR=/var/log/netsnap/archive
RETAIN_HOURS="${RETAIN_HOURS:-24}"
mkdir -p "$DIR"

tmp=$(mktemp "$DIR/.snap.XXXXXX")
trap 'rm -f "$tmp"' EXIT
{ date '+===== TAKEN %F %T %Z ====='; echo "===== REASON archive ====="; /usr/local/bin/netsnap.sh; } \
  > "$tmp" 2>&1 || true
[ -s "$tmp" ] || exit 0
mv -f "$tmp" "$DIR/snap-$(date '+%Y%m%d-%H%M%S').txt"
trap - EXIT

# Retention. -mmin rather than -mtime so the window is exact rather than rounded
# to whole days.
find "$DIR" -name 'snap-*.txt' -type f -mmin "+$((RETAIN_HOURS * 60))" -delete 2>/dev/null || true
echo "archive: $(find "$DIR" -name 'snap-*.txt' | wc -l) snapshots, $(du -sh "$DIR" 2>/dev/null | cut -f1) total"
