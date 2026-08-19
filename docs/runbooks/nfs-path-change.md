# Runbook: changing the NFS server or export path

Changing `nfs.server` or `nfs.path` in
`infra/services/nfs-provisioner/values.yaml` will **not** take effect on its own.
ArgoCD reports the Application as OutOfSync and the sync fails with:

```
PersistentVolume "pv-nfs-provisioner-..." is invalid:
spec.persistentvolumesource: Forbidden: spec.persistentvolumesource is immutable after creation
```

The chart bakes the server and path into a PersistentVolume, and that field
cannot be edited after creation. The PV has to be recreated, and it is held by a
chain of references that must be released in order.

## Order matters

Deleting the PV first appears to work and then hangs: the `pv-protection`
finalizer keeps it `Terminating` while its PVC exists, and the PVC is held by
the pod. Work from the pod end.

```sh
kubectl -n storage delete pod -l app=nfs-subdir-external-provisioner
kubectl -n storage delete pvc pvc-nfs-provisioner-nfs-subdir-external-provisioner
kubectl delete pv pv-nfs-provisioner-nfs-subdir-external-provisioner

# confirm all three are actually gone before continuing
kubectl get pv | grep nfs-provisioner || echo "clear"

kubectl -n argocd annotate app nfs-provisioner argocd.argoproj.io/refresh=hard --overwrite
```

ArgoCD recreates the PV with the new path within a minute or two. Verify what it
actually built, rather than that the sync went green:

```sh
kubectl get pv -o jsonpath='{range .items[*]}{.metadata.name} -> {.spec.nfs.path}{"\n"}{end}'
kubectl -n storage get pods
```

If a pod stays `Terminating`, `--force --grace-period=0` clears it; the mount it
is waiting on will never succeed, so there is nothing to lose.

**This affects only the 10 Mi helper volume the provisioner uses to reach the
export.** PersistentVolumes it has *provisioned* for workloads are separate, and
the `nfs` StorageClass uses `reclaimPolicy: Retain`, so their data survives.

## Getting the path right in the first place

The NFSv4 path is not the server-side filesystem path. OpenMediaVault exports
`/export` with `fsid=0`, making it the v4 pseudo-root, so clients address shares
relative to it — `/kubernetes-nfs-storage`, not `/export/kubernetes-nfs-storage`.

`showmount -e` is actively misleading here: it speaks the v3 protocol and prints
server-side paths, so it lists a path that a v4 mount rejects with `No such file
or directory`. Test from a node instead:

```sh
sudo mkdir -p /mnt/t
sudo mount -t nfs -o nfsvers=4.1 192.168.11.3:/kubernetes-nfs-storage /mnt/t && echo OK
sudo umount /mnt/t
```

Two failure messages worth telling apart:

| Message | Means |
|---|---|
| `access denied by server` | the export ACL does not include the client's subnet — fix in the OMV UI |
| `No such file or directory` | ACL is fine; the path is wrong — you probably included the pseudo-root prefix |
| `bad option ... need /sbin/mount.<type> helper` | `nfs-common` missing on the node — `homelab install` fixes it |
