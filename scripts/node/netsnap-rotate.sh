#!/bin/bash
# Take a snapshot, keeping exactly the two most recent.
#
#   /var/log/netsnap/netsnap.txt        the newest
#   /var/log/netsnap/netsnap_old.txt    the one before it
#
# Order matters. The new snapshot is collected to a temp file FIRST and only
# then rotated into place, so a node dying mid-collection cannot leave a
# truncated netsnap.txt with the previous one already discarded. The failure
# being investigated is one where the node stops responding, so "crashes
# halfway through" is the expected case, not an edge case.
set -euo pipefail

DIR=/var/log/netsnap
COLLECT=/usr/local/bin/netsnap.sh
mkdir -p "$DIR"

TMP=$(mktemp "$DIR/.netsnap.XXXXXX")
trap 'rm -f "$TMP"' EXIT

# `|| true`: a snapshot missing one section is far better than no snapshot. The
# blackhole makes some of these commands slow or unhappy, which is precisely
# when the output matters most.
{ date '+===== TAKEN %F %T %Z ====='; "$COLLECT"; } > "$TMP" 2>&1 || true

[ -s "$TMP" ] || exit 0
[ -f "$DIR/netsnap.txt" ] && mv -f "$DIR/netsnap.txt" "$DIR/netsnap_old.txt"
mv -f "$TMP" "$DIR/netsnap.txt"
trap - EXIT
