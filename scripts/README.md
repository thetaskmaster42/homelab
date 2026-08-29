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
