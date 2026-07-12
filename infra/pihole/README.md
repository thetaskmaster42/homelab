# Pi-hole

DNS and DHCP for `rps-home.com`. Runs on a dedicated Raspberry Pi.

## Responsibilities

- Local DNS: A records for all hosts and cluster ingress names (see
  [network/](../../network/README.md))
- DHCP: reservations for all permanent hosts
- Ad blocking for the network

## TODO

- [ ] Document the host (Pi model, OS, IP)
- [ ] Export/back up current local DNS records and DHCP reservations into this
      directory so the config is versioned
- [ ] Decide wildcard DNS approach for cluster ingress (`*.rps-home.com` via
      dnsmasq config vs. one record per app)
