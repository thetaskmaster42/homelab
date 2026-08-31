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
| `openbao-configure.sh` | the laptop | One-time OpenBao setup (audit, KV v2, Kubernetes auth), and per-application roles |
| `node/` | every node | Deployed by `homelab install`: minute-by-minute network snapshots, and the EEE-disable unit |
| `collect-crash-evidence.sh` | the laptop | Waits for a dead node to answer, then captures the *previous* boot's journal before it rolls over |
| `net-watchdog.sh` | a node | Detects the link-up-but-no-traffic blackhole and escalates: bounce the interface, then reboot |

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

## openbao-configure.sh

```sh
read -rs BAO_TOKEN && export BAO_TOKEN      # paste the root token; no echo
./scripts/openbao-configure.sh platform
./scripts/openbao-configure.sh app docmost docmost docmost
unset BAO_TOKEN
```

**The token goes in over stdin, never as an argument.** Anything in argv is
visible in `ps` inside the container while the command runs, and lands in shell
history on the way in.

Idempotent by design. `homelab nuke` destroys OpenBao's volume — it is on
`local-path` per [ADR 0008](../docs/decisions/0008-local-disk-for-observability-and-secrets.md)
— so re-running this after a rebuild is the expected path, not an edge case.

It refuses to do anything while OpenBao is sealed or uninitialised, and prints
the command that fixes it.

### Three decisions embedded in it

**Audit goes to stdout, not a file.** If every audit device fails, OpenBao stops
serving rather than serving unaudited — so a file device on a full 2Gi
`local-path` volume is an outage waiting to happen. stdout cannot fill the
volume and is already collected.

**Policies are written against `secret/data/<path>`, not `secret/<path>`.** KV v2
rewrites read paths, so a policy on `secret/foo` matches nothing and every read
is denied with no hint why. This is the most common KV v2 mistake.

**`bound_service_account_names` is never `*`.** That would let any pod in the
namespace assume the role, which discards most of the value of using the
ServiceAccount as the identity.

### Revoking root

`platform` writes an `admin` policy but does **not** revoke the root token. Set
up a `userpass` admin, verify you can actually log in with it, and only then
revoke root. Regenerate it on demand with `bao operator generate-root` when
genuinely needed rather than storing it.

## node/ — deployed to every node by `homelab install`

Not run by hand. `prereqs.ensure()` pushes these during install, which is what
makes them survive a rebuild — a diagnostic that only exists on the node you
happened to copy it to is not a diagnostic.

| File | On the node | What it does |
|---|---|---|
| `netsnap.sh` | `/usr/local/bin/` | Collects ~40 sections of network state |
| `netsnap-rotate.sh` | `/usr/local/bin/` | Takes one snapshot, keeping the two most recent |
| `netsnap.{service,timer}` | `/etc/systemd/system/` | Runs it every minute, on the minute |
| `eee-off@.service` | `/etc/systemd/system/` | Disables Energy Efficient Ethernet on the interface |

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
