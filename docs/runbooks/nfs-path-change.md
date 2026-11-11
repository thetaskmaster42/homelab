# Runbook: changing the NFS server or export path

There are two different things this can mean, and they need opposite handling.

| You want to… | Do this |
|---|---|
| Point the provisioner at a corrected path on the **same** data (e.g. a typo) | Recreate the helper PV — below |
| Move data to a **different** NAS or dataset | Do **not** edit `values.yaml`. Add a second provisioner and move each claim — see [ADR 0021](../decisions/0021-truenas-replaces-portal.md) |

A provisioned PersistentVolume's `spec.nfs.server` and `path` are immutable.
Editing `nfs.server` in `infra/services/nfs-truenas/values.yaml` therefore moves
nothing: every existing claim keeps pointing at the old server, and only *new*
claims land on the new one. If the old server then goes away, pods on the old
claims hang in `ContainerCreating` with `mount.nfs: Connection refused` — which
is exactly how ADR 0021's phase 4 was discovered.

## Recreating the provisioner's helper PV

Changing `nfs.server` or `nfs.path` will not take effect on its own. ArgoCD
reports the Application as OutOfSync and the sync fails with:

```
PersistentVolume "pv-nfs-truenas-..." is invalid:
spec.persistentvolumesource: Forbidden: spec.persistentvolumesource is immutable after creation
```

The chart bakes the server and path into a 10 Mi helper PV, held by a chain of
references that must be released in order.

### Order matters

Deleting the PV first appears to work and then hangs: the `pv-protection`
finalizer keeps it `Terminating` while its PVC exists, and the PVC is held by
the pod. Work from the pod end.

```sh
kubectl -n storage delete pod -l app=nfs-subdir-external-provisioner,release=nfs-truenas
kubectl -n storage delete pvc pvc-nfs-truenas-nfs-subdir-external-provisioner
kubectl delete pv pv-nfs-truenas-nfs-subdir-external-provisioner

# confirm all three are actually gone before continuing
kubectl get pv | grep nfs-truenas-nfs-subdir || echo "clear"

kubectl -n argocd annotate app nfs-truenas argocd.argoproj.io/refresh=hard --overwrite
```

ArgoCD recreates the PV with the new path within a minute or two. Verify what it
actually built, rather than that the sync went green:

```sh
kubectl get pv -o jsonpath='{range .items[*]}{.metadata.name} -> {.spec.nfs.server}:{.spec.nfs.path}{"\n"}{end}'
kubectl -n storage get pods
```

If a pod stays `Terminating`, `--force --grace-period=0` clears it; the mount it
is waiting on will never succeed, so there is nothing to lose.

This affects only the helper volume. PVs the provisioner has *provisioned* for
workloads are separate, and `reclaimPolicy: Retain` means their data survives.

## Moving a claim to a new class

The pattern ADR 0021 used, in two PRs:

1. **Add** a new PVC with a new name on the new class, unconsumed. A new name
   because `storageClassName` is immutable: editing it fails the sync, and if
   the app's Deployment is in a later sync wave it never gets applied.
2. **Copy** with a one-off pod that mounts both claims and runs `cp -a`
   (preserves owner and mode), then compare per-file `md5sum` and
   `stat -c '%n %u:%g %a %s'`. Directory sizes differ between ext4 and ZFS;
   file sizes must not. `du` is misleading on ZFS because of compression.
3. **Switch** `claimName` and delete the old PVC manifest.

If the Deployment carries a `sync-wave` later than the PVC, step 3 deadlocks:
ArgoCD prunes the old PVC at wave 0 and waits for it to go, `pvc-protection`
holds it while the running pod mounts it, and the pod is only replaced at the
later wave. Break it by applying the git version of the Deployment by hand
(`kubectl patch` the `claimName`); ArgoCD's own later wave is then a no-op.

## Getting the path right in the first place

On TrueNAS SCALE the NFSv4 path is the **full dataset path**
(`/mnt/tank/k8s-nfs`). OpenMediaVault was the reverse — it exported `/export`
with `fsid=0` as the v4 pseudo-root, so clients used paths relative to it — and
`showmount -e`, which speaks v3 and prints server-side paths, is misleading on
either. Test from a node instead:

```sh
sudo mkdir -p /mnt/t
sudo mount -t nfs -o nfsvers=4.1 192.168.11.4:/mnt/tank/k8s-nfs /mnt/t && echo OK
sudo umount /mnt/t
```

| Message | Means |
|---|---|
| `access denied by server` | the export's network list does not include the client's subnet |
| `No such file or directory` | access is fine; the path is wrong |
| `Connection refused` | nothing is serving NFS at that address — the NAS is down, or a claim still names an old server |
| `bad option ... need /sbin/mount.<type> helper` | `nfs-common` missing on the node — `homelab install` fixes it |
