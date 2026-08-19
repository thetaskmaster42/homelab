# Secrets

Two tiers, with one rule for choosing between them:

> **If ArgoCD needs it to bring the platform up, it is SOPS. If an application
> reads it while running, it is OpenBao.**

OpenBao starts sealed after every restart, so nothing on the path to a working
cluster may depend on it. That is why the platform's own credentials are
SOPS-encrypted in git rather than stored in the secret manager. See
[ADR 0004](decisions/0004-two-tier-secrets.md).

## One-time setup

### 1. Install the tools

```sh
# age — https://github.com/FiloSottile/age/releases
# sops — https://github.com/getsops/sops/releases
```

### 2. Generate your age key

```sh
mkdir -p ~/.config/sops/age
age-keygen -o ~/.config/sops/age/keys.txt
```

This prints a **public key** (`age1...`) and writes the private key to that
file. Back the file up somewhere outside this cluster — a password manager, not
a node. Losing it makes every encrypted file in this repo permanently
unreadable; leaking it makes them readable by whoever finds it, and this
repository is public.

### 3. Record the public key

Put it in `.sops.yaml`, replacing `REPLACE_WITH_YOUR_AGE_PUBLIC_KEY`. The public
key is safe to commit — it encrypts but cannot decrypt.

## Creating the bootstrap secrets

`clusters/rps/bootstrap-secrets.example.yaml` is the template. It holds
Grafana's admin login and the Tailscale operator's OAuth client — the two
credentials without which `kube-prometheus-stack` stays Degraded and
`tailscale-operator` cannot register a device.

**The encrypted file goes at `clusters/rps/bootstrap-secrets.enc.yaml`** — next
to `cluster.yaml`, because it is per-cluster and consumed by the CLI, not by
ArgoCD.

> **The output path is what matters, not the input path.** SOPS matches its
> creation rules against the file you hand it, and searches for `.sops.yaml`
> upward from *that* file. Encrypting a scratch copy in `/tmp` therefore fails
> with `error loading config: no matching creation rules found` — it never sees
> this repo's config, and `/tmp/bs.yaml` would not match `\.enc\.yaml$` anyway.

Use `--filename-override` so SOPS applies the rule for the destination while the
plaintext stays outside the repo entirely:

```sh
cp clusters/rps/bootstrap-secrets.example.yaml /tmp/bs.yaml
$EDITOR /tmp/bs.yaml                                     # fill in real values

sops --encrypt \
  --filename-override clusters/rps/bootstrap-secrets.enc.yaml \
  /tmp/bs.yaml > clusters/rps/bootstrap-secrets.enc.yaml

shred -u /tmp/bs.yaml
```

Or, if you would rather not remember that flag, edit at the destination and
encrypt in place — simpler, at the cost of a moment where plaintext sits at a
committable path:

```sh
cp clusters/rps/bootstrap-secrets.example.yaml clusters/rps/bootstrap-secrets.enc.yaml
$EDITOR clusters/rps/bootstrap-secrets.enc.yaml
sops --encrypt --in-place clusters/rps/bootstrap-secrets.enc.yaml
```

Do not commit between those last two commands. CI rejects a `*.enc.yaml` with no
sops metadata, so the mistake is caught — but caught after a push is too late on
a public repo.

Verify either way:

```sh
grep -q '^sops:' clusters/rps/bootstrap-secrets.enc.yaml && echo encrypted
sops --decrypt clusters/rps/bootstrap-secrets.enc.yaml | head
```

After that, never decrypt to disk again. Edit in place:

```sh
sops clusters/rps/bootstrap-secrets.enc.yaml
```

sops decrypts to a temp file, opens your editor, and re-encrypts on save, so the
plaintext never lands in the working tree where it could be committed.

## Applying them

```sh
uv run homelab bootstrap
```

This installs the age private key as `secret/sops-age` in the `argocd` namespace
— so ArgoCD can decrypt `*.enc.yaml` anywhere in the repo — then decrypts and
applies the bundle. Both steps are idempotent; re-running converges.

The age key is the one genuine chicken-and-egg in the design: the key that
decrypts the repository cannot be stored in the repository. Everything else
follows from that single manual step.

## Encrypting a service's values

Any file matching `*.enc.yaml` is covered by the creation rule in `.sops.yaml`.
Only the values under `data` and `stringData` are encrypted, so a diff still
shows that a Secret's name or namespace changed without revealing the payload.

```sh
sops --encrypt --in-place infra/services/<name>/secrets.enc.yaml
```

## What CI enforces

Guardrails, not conventions — on a public repo a mistake here is unrecoverable,
because rotating the credential is the only real remedy once it is pushed.

- no Kubernetes `Secret` with literal values may be committed unencrypted
- no sensitive key (`password`, `token`, `clientSecret`, …) may hold a literal
- every `*.enc.yaml` must actually carry sops metadata
- every `*.example.yaml` must contain only placeholders — a filled-in example is
  the exact accident this scheme exists to prevent, and it looks innocuous in
  review
- `gitleaks` scans history as a backstop

## If the key is lost or leaked

There is no recovery for a lost key. Re-key by generating a new one, updating
`.sops.yaml`, and recreating each encrypted file from its source of truth —
which is why what SOPS holds should always be reproducible from elsewhere
(regenerate the Grafana password, reissue the Tailscale OAuth client).

For a leak, treat every encrypted value as compromised and rotate it at the
source before re-keying.
