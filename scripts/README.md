# scripts/

Operator tools for diagnosing node-level failures. **Nothing here is deployed,
wired into the CLI, or run automatically.** They are hand-run, and that is
deliberate — see the status note below before assuming the cluster is protected
by them.

They live in git because they encode what a long debugging session established
about how these Pis fail, and that is the expensive thing to rediscover.

| Script | Runs on | What it does |
|---|---|---|
| `bump-app-version.sh` | the laptop | Repins an application's image tag in `apps/*/kustomization.yaml`, refusing anything that would not deploy |
| `node/` | every node | Deployed by `homelab install`: minute-by-minute network snapshots, and the EEE-disable unit |
| `collect-crash-evidence.sh` | the laptop | Waits for a dead node to answer, then captures the *previous* boot's journal before it rolls over |
| `net-watchdog.sh` | a node | Superseded by `node/netsnap-sentinel.sh`, which does the same escalation *after* capturing evidence |

## bump-app-version.sh

```sh
./scripts/bump-app-version.sh rh-dashboard 1.0.3
./scripts/bump-app-version.sh rh-dashboard 1.0.3 excalidraw 2026.09.01
./scripts/bump-app-version.sh --check rh-dashboard drawio
```

It edits the tag and stops. It does not commit and does not deploy — pushing to
`main` is the deploy, because ArgoCD reconciles from git, so what you get is a
reviewable one-line diff.

The value is in what it refuses. Every check below is a way this has actually
gone wrong at least once:

| Refuses | Because |
|---|---|
| a tag missing from the registry | merging a PR publishes nothing; the release workflow fires on the **tag** push |
| an image with no `linux/arm64` | every node here is arm64 — it would `CrashLoopBackOff` with `exec format error` |
| an overlay that also pins manifests to a commit sha | bumping the tag alone runs new code against old manifests. `prep-tracker` is this case, and its migration Job is exactly what would break |

It also strips a leading `v` and says so: a git tag of `v1.0.3` publishes an
*image* tag of `1.0.3`, because `release.yml` derives it with
`${GITHUB_REF_NAME#v}`. Pinning `v1.0.3` resolves to nothing and the pod sits in
`ImagePullBackOff` with no hint as to why.

Edits are textual, never a YAML round-trip. These kustomizations carry the
reasoning for every pin, and `safe_load`/`dump` would silently delete every
comment in them.

Exit status is 0 when everything asked for succeeded or was already current, and
1 if any app was refused — so it is safe to call from other automation.

## Why these exist

`k3s-server` had two distinct failure modes, and they need different evidence:

- **Network blackhole.** `eth0` reports 1Gbps/Full with zero error counters, the
  kernel is healthy and still logging, but no packet moves in either direction.
  The node stays that way until power-cycled — 402 minutes in the worst observed
  case. **The hardware watchdog does not catch this**: `bcm2835-wdt` only fires
  when the kernel stops petting it, and here the kernel is fine. Only the network
  path is dead. `net-watchdog.sh` therefore probes *reachability*, not liveness.
- **Instant death.** No warning, no NFS timeouts, the log simply stops mid-line.
  Nothing is recoverable in-process, so the goal is to capture the previous
  boot's journal afterwards — which is `collect-crash-evidence.sh`.

The link-bounce step in the watchdog is a diagnostic as much as a fix: if
bouncing the interface restores traffic, the fault is on the Pi's side
(driver/PHY) rather than the cable or switch. That distinction was not otherwise
obtainable.

## Status: probably fixed, kept anyway

The suspected causes — an under-spec PSU, mains instability, and 7-month-stale
bootloader EEPROM — have all been addressed: every Pi 5 now negotiates 5000 mA,
runs the Dec 2025 EEPROM, and sits behind a dedicated UPS.

Since then `k3s-server` has been up **4+ days continuously**, against a previous
pattern of dying every 28 to 132 minutes. That is strong evidence the problem is
resolved, though not proof.

These are kept because the failure modes are documented here and nowhere else,
and because "it stopped happening" is not the same as "we know why it stopped".
If a node wedges again, start with `collect-crash-evidence.sh`.

## If you want the watchdog running

It is not a systemd unit yet, on purpose — an automatic reboot loop is a bad
thing to add to a cluster that is currently stable. Making it one means writing a
unit file, deciding where it is deployed from (the CLI installs node-level
prerequisites, so `steps/prereqs.py` is the honest place), and accepting that it
can reboot a node unattended.

## Usage

```sh
# Wait for a crashed node and collect the post-mortem
./collect-crash-evidence.sh 192.168.11.7
OUT_DIR=~/crash WAIT_SECONDS=3600 ./collect-crash-evidence.sh

# Run the watchdog on a node (foreground; needs root for `ip link` and reboot)
sudo PROBE_IPS="192.168.11.1 192.168.11.3" FAIL_THRESHOLD=6 ./net-watchdog.sh
```

## node/ — deployed to every node by `homelab install`

Not run by hand. `prereqs.ensure()` pushes these during install, which is what
makes them survive a rebuild — a diagnostic that only exists on the node you
happened to copy it to is not a diagnostic.

| File | On the node | What it does |
|---|---|---|
| `netsnap.sh` | `/usr/local/bin/` | Collects ~40 sections of network state |
| `netsnap-sentinel.sh` | `/usr/local/bin/` | Heartbeat, failure capture, and recovery escalation |
| `netsnap-preboot.sh` | `/usr/local/bin/` | Preserves the previous boot's snapshot before anything overwrites it |
| `netsnap-archive.sh` | `/usr/local/bin/` | Half-hourly archive, pruned at 24h |
| `netsnap-{preboot,sentinel,archive}.service` | `/etc/systemd/system/` | Units for the above |
| `netsnap-archive.timer` | `/etc/systemd/system/` | Fires the archive every 30 minutes |
| `99-disable-eee.rules` | `/etc/udev/rules.d/` | Disables Energy Efficient Ethernet as the interface appears |

### Why

The nodes intermittently stop passing traffic while the interface still reports
`1000Mb/s Full, Link detected: yes` with **every error counter at zero**, no ARP
resolving to anything on the LAN, and nothing whatsoever in the kernel log. All
three nodes have done it. Recovery needs a reboot — which resets every counter,
destroying the evidence. So the state has to be captured while it is still
broken, on the node itself, because the node is unreachable at the time.

Snapshots land in `/var/log/netsnap/`:

```
netsnap.txt        the newest
netsnap_old.txt    the one before it
```

**Two files at one-minute cadence covers a two-minute window.** Every outage so
far has lasted hours, so in practice both files will be from *during* the
outage and the last healthy snapshot is gone. That is a real limitation of
keeping two. If the comparison matters more than the recency, raise the count or
keep a separate last-known-good written only while the gateway still resolves.

The new snapshot is collected to a temp file and only then rotated into place,
so a node dying mid-collection cannot leave a truncated `netsnap.txt` with the
previous one already discarded. Given the failure being investigated, "dies
halfway through" is the expected case.

~58KB per snapshot is about 82MB/day. These boot from SD, so it is worth
knowing, but it is far below the cards' rated endurance and only two files exist
at a time.

### EEE

`eee-off@eth0.service` is bound to the *device*, not to boot, so it re-applies
whenever the interface reappears — including after a link bounce.

It is a **hypothesis under test, not a fix.** EEE was enabled on these NICs while
the switch reported no EEE support back, and a PHY that enters Low Power Idle
and fails to wake the datapath cleanly produces exactly the observed signature.
That is consistent, not proven. If the blackholes continue with EEE off, it is
ruled out and the snapshots are what move the search forward.

## The sentinel

`netsnap-sentinel.sh` merges three jobs that were previously separate, because
separating them loses the evidence:

1. **Heartbeat.** Probes this node's peers (rendered into a systemd drop-in from
   `cluster.yaml` by `homelab install`) every 10s. It is reachable if *any*
   target answers — all of them failing is what distinguishes "this node is
   isolated" from "one peer is down".
2. **Capture on transition.** After 6 consecutive failures it writes
   `failure/failure-<ts>.txt` and copies the last healthy snapshot alongside it
   as `lastgood-<ts>.txt`. The pairing is the point: a snapshot of a broken node
   means little without the same 40 sections taken while it worked.
3. **Staged recovery**, cheapest first, recording which step restored traffic:

   | failures | stage | resets | if this is what fixes it |
   |---|---|---|---|
   | 9 (~90s) | `ip neigh flush` | this kernel's ARP table only | stale neighbour state; NIC and switch were fine |
   | 12 (~2m) | `ethtool -r` | PHY autonegotiation | link negotiation, not ARP |
   | 15 (~2.5m) | `ip link down/up` | MAC + PHY + ARP + switch re-learn | driver state, or the switch port |
   | 90 (~15m) | reboot | everything | |

   The staging exists because a link bounce resets **three things at once** --
   the Pi's MAC/PHY, the ARP cache (`arp_evict_nocarrier=1`), and the switch's
   view of the port. "A bounce fixed it" therefore cannot say which was stuck.
   Escalating in order of blast radius decomposes that.

   One caveat the log states explicitly: recovery *after* a stage is not proof
   that the stage caused it -- the fault may clear on its own at that moment.
   One event proves nothing. The same stage clearing it repeatedly, while the
   cheaper stages before it did not, is what identifies the layer.

The merge is deliberate. **A link bounce resets the interface counters**, so any
recovery attempt destroys the evidence for the failure that triggered it. Only
one process can guarantee capture happens first.

### What the previous design got wrong

The old `netsnap.timer` took a snapshot every minute and kept two. That is a
two-minute window, and the recovery reboot consumed it every time — worker-1's
outage snapshots were overwritten two minutes after it came back, which is why
five blackholes produced no usable evidence. `netsnap-preboot.service` now
preserves the previous boot's snapshot into `preboot/` before anything runs, and
`failure/` is never pruned.

### The in-failure counter window

`failure/counters-<ts>.txt` samples the driver counters twice, `COUNTER_WINDOW`
seconds apart, entirely inside the failure and *before* any recovery attempt.

This exists because the `failure`/`lastgood` pair cannot answer the question
that matters. `last-good.txt` is refreshed every `GOOD_INTERVAL` (5m), so the
delta between it and the failure snapshot spans mostly-healthy traffic and is
dominated by normal work. Two samples taken while the node is isolated are not.

Read it as:

| observation | meaning |
|---|---|
| `tx_` climbing, `rx_` flat | frames leave, nothing comes back -- receive-path fault |
| both flat (file lists nothing) | the interface is wedged entirely |
| both climbing | the link carries traffic; the fault is above it |

### Testing it

`NETSNAP_DIR` redirects all output, so the capture path can be exercised against
a scratch directory without corrupting real evidence or rebooting a node:

```sh
sudo env NETSNAP_DIR=/tmp/nstest PROBE_IPS="192.0.2.1" \
     INTERVAL=1 FAIL_THRESHOLD=3 BOUNCE_AFTER=0 REBOOT_AFTER=0 \
     timeout 10 ./netsnap-sentinel.sh
```

`BOUNCE_AFTER=0` and `REBOOT_AFTER=0` disable the two steps that touch the real
machine, leaving detection and capture under test.
