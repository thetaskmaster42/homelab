# Hardware

Everything in the lab sits on the `192.168.11.0/24` subnet, behind the Gateway
Pi at `192.168.11.1` which routes to the house network on `192.168.0.0/24`.

## k3s cluster

| Node | IP | Role | Machine |
|---|---|---|---|
| `k3s-server` | 192.168.11.7 | control plane | Raspberry Pi, 16 GB |
| `k3s-worker-1` | 192.168.11.6 | worker | Raspberry Pi, 16 GB |
| `k3s-worker-2` | 192.168.11.5 | worker | Raspberry Pi, 16 GB |

All three run Ubuntu 26.04, **arm64**. There is no amd64 node — every image
deployed here must have a `linux/arm64` manifest, which CI enforces.

The machine-readable version of this table is
[`clusters/rps/cluster.yaml`](../clusters/rps/cluster.yaml). That file is the
input to the CLI; this page is for humans. If they disagree, the YAML wins.

### Resource asymmetry that matters

RAM is uniform and generous (3 x 16 GB, typically under 15% used). **Disk is
not:**

| Node | Ephemeral storage |
|---|---|
| `k3s-server` | ~115 Gi |
| `k3s-worker-1` | ~117 Gi |
| `k3s-worker-2` | **~57 Gi** |

The default `local-path` StorageClass pins a PersistentVolume to whichever node
first scheduled its pod, and that pod can then never move. `prep-tracker`'s PVC
is already bound to `k3s-worker-2`, the smallest node. Large PVCs landing there
risk `DiskPressure` evictions, which is why Prometheus retention is capped at
7d/10Gi and why an NFS provisioner is the fix rather than a nicety.

## Other hosts

| Host | IP | Role |
|---|---|---|
| `portal` | 192.168.11.3 | OpenMediaVault NAS — NFS (2049) and a web UI on 80 |
| `Gateway` | 192.168.11.1 | Routes 192.168.11.0/24 to 192.168.0.0/24; DHCP for the lab |

Neither is part of the cluster. `portal` is intended to back an NFS
StorageClass so stateful workloads stop being node-pinned — that needs its
export path, which is not yet recorded here.

The Gateway Pi is a single point of failure for the entire lab subnet and is not
yet under configuration management.

## Network

- Lab subnet: `192.168.11.0/24`, gateway `192.168.11.1`
- MetalLB pool: `192.168.11.200-192.168.11.250` — must stay outside the
  Gateway's DHCP scope
- Traefik ingress VIP: `192.168.11.240`, pinned so it survives a rebuild
- Tailnet: `tailcb5a3f.ts.net` — how anything is reached from outside the LAN

There is no local DNS server. Hosts are addressed by IP on the LAN and by
MagicDNS name on the tailnet.
