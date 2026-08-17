# 0003 — Drop step-ca; Tailscale for external, a local CA chain for LAN

**Status:** accepted, 2026-08-17

## Context

The previous design ran [smallstep step-ca](https://smallstep.com/docs/step-ca/)
in an LXC container as a private certificate authority, with cert-manager
requesting certificates from it over ACME. That container no longer exists — the
Proxmox host it ran on is gone — and the `ClusterIssuer` left behind in the repo
had an empty `caBundle` and a hostname that never resolved.

## Decision

Do not rebuild it.

**External exposure** is via Tailscale, which provisions real Let's Encrypt
certificates for `*.tailcb5a3f.ts.net` on both tailnet-internal and Funnel
hostnames. That removes the entire reason a private CA existed: browsers and
phones trust these certificates with no trust-store changes on any device.

**LAN-internal TLS** on the MetalLB VIP uses cert-manager with a local
`selfsigned -> CA` chain: a self-signed root issues one CA certificate, and that
CA signs every leaf. Trusting one root on the laptop covers every LAN service.

The step-ca setup notes are kept at [`docs/step-ca-setup.md`](../step-ca-setup.md)
as reference — they document several real failure modes worth not rediscovering.

## Consequences

- One fewer stateful service to run, back up, and rotate keys for.
- No dependency on a host outside the cluster for certificate issuance.
- The trade: LAN-internal certificates are trusted only by devices that have
  imported the homelab root. Anything needing broad trust goes over the tailnet
  instead, which is the better path anyway.
