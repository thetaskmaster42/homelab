# 0004 — Two tiers of secret: SOPS+age in git, OpenBao at runtime

**Status:** **superseded** by [ADR 0014](0014-sops-as-the-only-secret-manager.md), 2026-08-31.

> The second tier was built and never used: no application ever read a
> secret from OpenBao. Automating its unseal then put the unseal keys in
> SOPS, making its protection at rest equal to the first tier's rather
> than better. SOPS+age is now the only secret manager. The reasoning
> below is kept because it is still the right argument for a cluster that
> has a runtime-secret consumer — this one does not.

**Originally accepted:** 2026-08-17

## Context

ArgoCD reconciles from git, so anything it needs must be in git. This repository
is public. Those two facts together force encryption-at-rest for any credential
ArgoCD consumes.

Separately, applications need secrets — database passwords, API tokens — and
putting those in git at all is worse than not having to.

These are different problems and one mechanism does not solve both well.

## Decision

Two tiers, with a clear rule for which to use.

**SOPS + age — secrets that must live in git.** Bootstrap-level only: the
Tailscale OAuth client, a registry pull secret, the age key itself. Encrypted
with age, committed as `*.enc.yaml`, decrypted by ArgoCD's repo-server. Small,
static, rarely rotated.

**OpenBao — secrets applications read at runtime.** Never committed anywhere.
Rotatable without a deploy. Can be issued dynamically, which is the real prize:
the database secrets engine can hand each application a short-lived PostgreSQL
role instead of everyone sharing one static password.

The rule: **if ArgoCD needs it to bring the platform up, it is SOPS. If an
application needs it while running, it is OpenBao.**

## Consequences

- There is a bootstrap chain, and it terminates in something manual. The age key
  unlocks the repo; OpenBao's unseal keys unlock OpenBao; neither can be stored
  in what it unlocks. See [the unseal runbook](../runbooks/openbao-unseal.md).
- OpenBao is sealed after every restart, so it is not a dependency the platform
  can bring up unattended. Nothing required to reach a working cluster may
  depend on it — that is why the Tailscale OAuth client stays in SOPS.
- Two mechanisms is more to learn than one. That is the point here, but it is a
  real cost, and consolidating on SOPS alone would be defensible for a homelab
  that only wanted things to work.
