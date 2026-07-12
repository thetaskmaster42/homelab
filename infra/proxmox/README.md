# Proxmox

HP desktop running Proxmox VE. Hosts:

- **k3s control-plane** — one container (LXC) or VM
- **k3s workers** — additional containers alongside the Raspberry Pi workers

## Notes

- LXC containers need extra config to run k3s (cgroups, `/dev/kmsg`,
  swap accounting). A VM is simpler if resources allow — decide during
  cluster provisioning.

## TODO

- [ ] Document host specs (CPU/RAM/storage) and Proxmox version
- [ ] Define the CT/VM inventory for the cluster (IDs, resources, IPs)
- [ ] Capture CT/VM creation as scripts or config here
