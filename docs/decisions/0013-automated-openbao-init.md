# 0013 — Automated OpenBao init, with the unseal keys in SOPS

**Status:** accepted, 2026-08-31

## Context

OpenBao starts sealed and uninitialised. Its volume is on `local-path`
([ADR 0008](0008-local-disk-for-observability-and-secrets.md)), so `homelab nuke`
destroys it, and every rebuild required the full ceremony by hand: run
`bao operator init`, copy three keys out of a terminal, store them, then paste
two back in to unseal.

That is a human step in the critical path of a cluster whose entire premise is
that `nuke && install` is a routine drill rather than an emergency. It also
meant OpenBao was, in practice, never configured — it sat sealed and empty
through several rebuilds because the ceremony was never quite worth it.

## Decision

`homelab bootstrap` initialises and unseals OpenBao automatically. The keys are
generated, encrypted with age, and written to
`clusters/<name>/openbao-unseal.enc.yaml`. That file is **not** applied to the
cluster; it is read back only to unseal after a rebuild. `homelab openbao`
re-runs the same idempotent logic standalone.

3 shares, threshold 2 — unchanged. For one operator, 5/3 is ceremony and 1/1 has
no redundancy.

## What this costs

**The age key becomes the single root of trust.** OpenBao's unseal keys now live
in the same repository, protected by the same key, as the SOPS bootstrap
secrets. Its protection *at rest* is therefore equal to SOPS's, not better than
it. Anyone holding the age key holds OpenBao.

This is worth being blunt about, because it partially undercuts
[ADR 0004](0004-two-tier-secrets.md). That ADR separated "secrets that must live
in git" from "secrets applications read at runtime", and the second tier was
meant to be stronger. On the at-rest axis, it no longer is.

What survives, and why this is a trade rather than a surrender:

- **Dynamic credentials.** The database engine can issue a short-lived
  PostgreSQL role per request instead of a static shared password. Nothing about
  key storage weakens that.
- **Rotation without a redeploy.** Changing a secret does not mean a commit, a
  sync and a pod restart.
- **Per-application policies**, bound to Kubernetes ServiceAccounts, so a
  compromised pod reaches only its own secrets.
- **An audit log** of who read what.

Those are the reasons OpenBao is here. None of them depend on the unseal keys
being harder to reach than the SOPS bundle.

### The upgrade that undoes the cost

Auto-unseal against an external KMS. The unseal keys then stop existing at all —
OpenBao asks the KMS to decrypt its root key at startup and there is nothing to
store. That restores the separation ADR 0004 intended, at the price of a cloud
dependency (~$1/month) that this cluster does not currently have. It is the
right next step, not a hypothetical one.

## Consequences

- **A rebuild needs no human for OpenBao.** `nuke && install && bootstrap`
  produces an initialised, unsealed store.
- **The keys file must not be lost.** If it is, and OpenBao is initialised, the
  store cannot be opened — the only recovery is deleting the PVC and starting
  over. `load()` says exactly that rather than failing obscurely.
- **Bootstrap waits for the pod** rather than assuming it. OpenBao is deployed by
  ArgoCD, so it does not exist when the root Application is applied. A slow
  first sync degrades to a warning and `homelab openbao`, never a failed
  bootstrap.
- **Plaintext never touches disk.** sops reads it on stdin; only ciphertext is
  written. This is why that one call bypasses `Runner`, which has no stdin — the
  alternatives were keys in argv (logged, and visible in `ps`) or a plaintext
  temp file.
- **`ADR 0008`'s "reconstructible" claim is now true again.** Losing OpenBao's
  volume costs a re-init and a re-seed, which is automated — provided the keys
  file survives, and it is in git.
