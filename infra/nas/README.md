# NAS — portal

Raspberry Pi running **OpenMediaVault**, providing NFS and general NAS
services to the network.

The k3s cluster will consume NFS from portal for persistent volumes (via the
`nfs-subdir-external-provisioner` or `csi-driver-nfs` — defined under
`cluster/infrastructure/`).

## TODO

- [ ] Document hardware, disks, and OMV version
- [ ] Document NFS exports (paths, allowed hosts, options)
- [ ] Create a dedicated export for k3s persistent volumes
