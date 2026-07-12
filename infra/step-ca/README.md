# step-ca

[smallstep step-ca](https://smallstep.com/docs/step-ca/) running in an LXC
container on Proxmox. Private certificate authority for `rps-home.com`.

## Consumers

- **cert-manager** (in the k3s cluster) requests certificates over ACME —
  see `cluster/infrastructure/cert-manager-issuers/step-ca-issuer.yaml`.
  Requires an ACME provisioner on the CA:

  ```sh
  step ca provisioner add acme --type ACME
  ```

- Any host on the network can trust the root:

  ```sh
  step ca root root_ca.crt --ca-url https://step-ca.rps-home.com
  ```

## TODO

- [ ] Document the LXC container (hostname, IP, Pi-hole record)
- [ ] Confirm listen address/port and ACME provisioner name
- [ ] Export the root certificate into the ClusterIssuer `caBundle`
- [ ] Back up the CA config (`ca.json`) and document the key backup strategy
