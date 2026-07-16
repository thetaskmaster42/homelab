# Homelab Private CA with step-ca

Private certificate authority for the homelab, running [smallstep step-ca](https://smallstep.com/docs/step-ca/) in an LXC container. Issues TLS certificates for internal services (Pi-hole, and later Proxmox / reverse proxies) that are trusted by all homelab devices.

## Topology

| Node | Role | Notes |
|------|------|-------|
| `ca` (LXC container, `192.168.0.4`) | step-ca server | Listens on port **9000** |
| Raspberry Pi | Pi-hole (DNS + DHCP) | Web UI served by `pihole-FTL` on 80/443 |
| Mac | Client / admin machine | Trusts the root CA |

CA hostname: `ca` reachable at `https://192.168.0.4:9000` (IP added as SAN — see Issue 3).
Pi-hole cert hostname: `pihole.rps-home.com` (resolved internally via Pi-hole Local DNS).

---

## 1. step-ca init (port 9000 instead of 443)

Initialize the CA. `STEPPATH` should point at the final location **before** running init, so the generated `ca.json` contains correct absolute paths:

```bash
export STEPPATH=/etc/step-ca
step ca init
```

Init generates:

- `certs/root_ca.crt` — root certificate, distributed to every client
- `certs/intermediate_ca.crt` — signs leaf certs
- `secrets/` — encrypted private keys
- `config/ca.json` — main config (address, dnsNames, provisioners)
- A default JWK (password-based) provisioner

Change the listen port in `/etc/step-ca/config/ca.json`:

```json
"address": ":9000"
```

### Issue: port 443 vs 9000

Port 443 is privileged (<1024), so binding it as the non-root `step` service user
would require `AmbientCapabilities=CAP_NET_BIND_SERVICE` in the systemd unit.
Moving to **9000** avoids that entirely — the service runs unprivileged with no
extra grants. All clients must then include `:9000` explicitly in every URL.

### Issue: relocating STEPPATH after init

An earlier init was done under `/root/.step` and files were copied to
`/etc/step-ca`. This fails two ways:

1. `password.txt` / config files owned by root → `permission denied` when the
   systemd unit runs as `User=step`.
2. `ca.json` still contains hardcoded `/root/.step/...` paths (root, crt, key,
   badger `db`) → `Invalid Dir: "/root/.step/db": stat ... permission denied`.

Fix is either `sed -i 's|/root/.step|/etc/step-ca|g'` on `ca.json` +
`defaults.json` plus `chown -R step:step /etc/step-ca`, **or** (what was done
here) a clean re-init with `STEPPATH=/etc/step-ca` set from the start.
Note: a clean re-init generates a **new root with a new fingerprint** — any
previously trusted clients must remove the old root and re-bootstrap.

### systemd service

```ini
# /etc/systemd/system/step-ca.service
[Unit]
Description=step-ca
After=network.target

[Service]
User=step
Environment=STEPPATH=/etc/step-ca
ExecStart=/usr/bin/step-ca /etc/step-ca/config/ca.json --password-file /etc/step-ca/password.txt
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
useradd --system --home /etc/step-ca --shell /bin/false step
echo "<ca-password>" > /etc/step-ca/password.txt
chown -R step:step /etc/step-ca
chmod 600 /etc/step-ca/password.txt
systemctl daemon-reload
systemctl enable --now step-ca
curl -k https://localhost:9000/health   # {"status":"ok"}
```

---

## 2. Add the ACME provisioner

On the CA container:

```bash
step ca provisioner add acme --type ACME
systemctl restart step-ca
curl -k https://localhost:9000/acme/acme/directory
```

The directory URL pattern is `https://<ca>:9000/acme/<provisioner-name>/directory`.
ACME lets clients like Caddy / Traefik obtain and renew certs automatically with
no passwords — the client proves control of the hostname via HTTP-01 or
TLS-ALPN-01 challenges, and the CA connects **back** to the client during
validation (so internal DNS must resolve the requested hostname to the client).

The original JWK provisioner from init remains available and is used where ACME
challenges are impractical (see Issue 6).

---

## 3. Add the root CA to clients

On each client, verify + download the root and record the CA URL:

```bash
step ca bootstrap --ca-url https://192.168.0.4:9000 --fingerprint <fingerprint>
```

Get the fingerprint on the CA host:

```bash
step certificate fingerprint /etc/step-ca/certs/root_ca.crt
```

Install into the OS trust store:

```bash
# Debian / Ubuntu / Raspberry Pi OS
cp root_ca.crt /usr/local/share/ca-certificates/homelab-root.crt
update-ca-certificates
```

### Issue: `x509: cannot validate certificate ... doesn't contain any IP SANs`

The CA's own TLS listener cert only contained the DNS names given during init.
Connecting via `https://192.168.0.4:9000` failed TLS validation. Fixed by adding
the IP to `dnsNames` in `ca.json` (step-ca emits it as an IP SAN) and restarting:

```json
"dnsNames": ["ca", "localhost", "192.168.0.4"]
```

Longer-term, prefer a DNS name (e.g. via a Pi-hole Local DNS record) so client
configs survive an IP change; keep the IP SAN as a fallback so the CA does not
depend on Pi-hole being up.

---

## 4. Validation from the Mac

```bash
brew install step
step ca bootstrap --ca-url https://192.168.0.4:9000 --fingerprint <fingerprint>
step certificate install $(step path)/certs/root_ca.crt   # adds to System Keychain
```

Manual alternative: `sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain root_ca.crt`.

Verify (no `-k` flag — this proves trust works):

```bash
curl https://192.168.0.4:9000/health
```

Notes:

- After the clean re-init (new root), the **old** root had to be removed from
  Keychain and bootstrap re-run with `--force`.
- Firefox ignores the system keychain — either import the root in Firefox's own
  cert settings or set `security.enterprise_roots.enabled = true`.

---

## 5. Create key and cert for the Pi-hole web server

On the Raspberry Pi:

```bash
# install step CLI (arm build)
wget https://github.com/smallstep/cli/releases/latest/download/step-cli_arm64.deb
sudo dpkg -i step-cli_arm64.deb

step ca bootstrap --ca-url https://192.168.0.4:9000 --fingerprint <fingerprint>

sudo mkdir -p /etc/pihole/tls
sudo step ca certificate pihole.rps-home.com \
  /etc/pihole/tls/pihole.crt /etc/pihole/tls/pihole.key \
  --provisioner <jwk-provisioner-name>
```

A Pi-hole Local DNS record maps `pihole.rps-home.com` → the Pi's LAN IP, so all
DHCP clients resolve it automatically.

---

## 6. Issue: ACME HTTP-01 fails on Pi-hole — port 80 already in use

First attempt used the ACME provisioner in standalone mode:

```
Using Standalone Mode HTTP challenge to validate pihole.rps-home.com
ListenAndServe(): listen tcp :80: bind: address already in use
error validating ACME Challenge ... INTERNAL_ERROR; received from peer
```

Root cause: ACME HTTP-01 **requires** a challenge listener on port 80 (fixed by
the protocol), but Pi-hole's own web server already owns ports 80/443. The
`INTERNAL_ERROR` from the CA was just fallout from the failed challenge.

Resolution: use the **JWK provisioner** for this host instead (`--provisioner`
flag as in section 5). JWK authenticates with the provisioner password and needs
no inbound listener at all, so port conflicts are irrelevant. Renewals via
`step ca renew` authenticate with the existing cert — no password — so
automation still works unattended.

Rule of thumb adopted: **JWK for hosts that own ports 80/443 themselves
(Pi-hole); ACME for services that manage their own challenges (Caddy, Traefik).**

---

## 7. Combine cert + key into the PEM Pi-hole expects

Pi-hole v6's `pihole-FTL` serves the web UI directly and reads **one combined
PEM** (key first, then cert) at `/etc/pihole/tls.pem`:

```bash
sudo bash -c 'cat /etc/pihole/tls/pihole.key /etc/pihole/tls/pihole.crt > /etc/pihole/tls.pem'
sudo chown pihole:pihole /etc/pihole/tls.pem
sudo chmod 600 /etc/pihole/tls.pem

sudo pihole-FTL --config webserver.domain pihole.rps-home.com
sudo systemctl restart pihole-FTL
```

Verify:

```bash
openssl x509 -in /etc/pihole/tls/pihole.crt -noout -dates -subject
curl -v https://pihole.rps-home.com/admin
```

Browsing to `https://pihole.rps-home.com/admin` from a trusted client shows a
clean padlock. Hitting the raw IP will warn — the cert has no IP SAN, use the
hostname.

---

## 8. Automated renewal (script + cron)

step-ca issues short-lived certs by design, so renewal automation is mandatory.
The script renews, rebuilds the combined PEM, and reloads FTL:

```bash
# /usr/local/bin/renew-pihole-cert.sh
#!/bin/bash
export STEPPATH=/root/.step
step ca renew --force /etc/pihole/tls/pihole.crt /etc/pihole/tls/pihole.key \
  && cat /etc/pihole/tls/pihole.key /etc/pihole/tls/pihole.crt > /etc/pihole/tls.pem \
  && chown pihole:pihole /etc/pihole/tls.pem \
  && systemctl restart pihole-FTL
```

```bash
sudo chmod +x /usr/local/bin/renew-pihole-cert.sh
sudo /usr/local/bin/renew-pihole-cert.sh && echo OK   # test once manually
```

Cron (root, `sudo crontab -e`):

```cron
0 3 * * * /usr/local/bin/renew-pihole-cert.sh >> /var/log/pihole-cert-renew.log 2>&1
```

Notes:

- `step ca renew` authenticates with the existing cert — fully unattended, no
  password prompt.
- `STEPPATH` must be exported because cron runs with a minimal environment and
  step needs the bootstrap config (CA URL, root) written during `step ca bootstrap`.
- Daily at 03:00 suits 90-day certs; if certs are the 24h default, run every few
  hours (`0 */8 * * *`) or raise `maxTLSCertDuration` in the provisioner claims
  in `ca.json`.

---

## Issues summary

| # | Symptom | Root cause | Fix |
|---|---------|-----------|-----|
| 1 | systemd: `error reading password.txt: permission denied` | Unit runs as `step`, files owned by root | `chown -R step:step /etc/step-ca` |
| 2 | `Invalid Dir: "/root/.step/db" ... permission denied` | `ca.json` paths hardcoded to old `STEPPATH` | Rewrite paths / clean re-init with correct `STEPPATH` |
| 3 | `x509: ... doesn't contain any IP SANs` | CA listener cert lacked the IP | Add `192.168.0.4` to `dnsNames`, restart step-ca |
| 4 | ACME: `listen tcp :80: bind: address already in use` | Pi-hole owns port 80; HTTP-01 needs it | Use JWK provisioner for Pi-hole cert |
| 5 | Stale trust after re-init | New root = new fingerprint | Remove old root on clients, re-bootstrap `--force` |

## Next steps

- [ ] Issue cert for Proxmox web UI
- [ ] Point Traefik/Caddy at the ACME directory (`https://192.168.0.4:9000/acme/acme/directory`) for zero-touch service certs
- [ ] Consider raising `maxTLSCertDuration` (e.g. `2160h`) in provisioner claims
- [ ] Back up `/etc/step-ca` (especially `secrets/` and the CA password)