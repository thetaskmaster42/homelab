# NAS — portal

Raspberry Pi running **OpenMediaVault**, providing NFS and general NAS
services to the network.

The k3s cluster uses the built-in Rancher `local-path` storage class for
persistent volumes for now — NFS from portal is not consumed by the cluster.
If shared/replicated storage is needed later, an NFS provisioner can be added
under `cluster/infrastructure/`.

## TODO

- [ ] Document hardware, disks, and OMV version
- [ ] Document NFS exports (paths, allowed hosts, options)
