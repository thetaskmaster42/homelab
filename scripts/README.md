# scripts/

Operator tools for diagnosing node-level failures. **Nothing here is deployed,
wired into the CLI, or run automatically.** They are hand-run, and that is
deliberate — see the status note below before assuming the cluster is protected
by them.

They live in git because they encode what a long debugging session established
about how these Pis fail, and that is the expensive thing to rediscover.

| Script | Runs on | What it does |
|---|---|---|
| `collect-crash-evidence.sh` | the laptop | Waits for a dead node to answer, then captures the *previous* boot's journal before it rolls over |
| `net-watchdog.sh` | a node | Detects the link-up-but-no-traffic blackhole and escalates: bounce the interface, then reboot |

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
