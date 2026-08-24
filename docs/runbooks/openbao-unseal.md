# Runbook: unsealing OpenBao

OpenBao starts **sealed** after every restart — pod eviction, node reboot, chart
upgrade, cluster rebuild. While sealed it answers health checks but serves no
secrets, so anything depending on it fails until a human intervenes.

There is no cloud KMS here to auto-unseal against. That is the cost of running
your own secret manager, and it is worth understanding rather than designing
around.

## First time only: initialise

```sh
kubectl -n openbao exec -it openbao-0 -- bao operator init \
  -key-shares=3 -key-threshold=2
```

This prints **three unseal keys and one root token, exactly once**. They are
never recoverable afterwards.

Store them somewhere that does not depend on this cluster — a password manager,
not a file on a node, and never in this repository. Losing them means losing
every secret OpenBao holds; leaking them means losing them to whoever finds
them, and this repo is public.

`-key-threshold=2` means any two of the three keys unseal it. That is one
operator with a backup, not real Shamir key-splitting across people, but it
survives losing one key.

## Every restart: unseal

```sh
kubectl -n openbao exec -it openbao-0 -- bao operator unseal   # key 1
kubectl -n openbao exec -it openbao-0 -- bao operator unseal   # key 2
kubectl -n openbao exec -it openbao-0 -- bao status            # Sealed: false
```

## Check whether this is your problem

```sh
kubectl -n openbao get pods
kubectl -n openbao exec openbao-0 -- bao status
```

A pod that is `Running` but `0/1 Ready` with `Sealed: true` is the normal sealed
state, not a fault. Applications using the injector will show init containers
stuck waiting for a secret — that is the downstream symptom.

## Why it is on NFS, despite wanting local disk

It used to be on `local-path`, for good reasons: the secret store should not
depend on the NAS being reachable, and OpenBao's file backend wants local fsync
semantics. [ADR 0006](../decisions/0006-nfs-default-storage.md) removed
`local-path` from the cluster entirely, so `nfs` is now the only option.

That is tolerable because of how it fails and how it recovers. The mount is
`hard`, so a NAS outage *blocks* OpenBao rather than corrupting it — the pod
hangs in uninterruptible sleep and comes back when `portal` does. And the
recovery path has not changed: if the volume is lost, re-initialise and re-seed
from the SOPS-encrypted bootstrap bundle. That still means what OpenBao holds
must be reproducible, never the only copy of anything.

The practical consequence: **`portal` being down now blocks the unseal itself.**
Check the NAS before debugging a sealed OpenBao that will not start.

## Where this goes next

Auto-unseal needs a trusted external key source. Two homelab-viable options:

1. **Transit seal against a second OpenBao** — moves the problem rather than
   solving it, unless the second one lives outside the cluster.
2. **A cloud KMS** — genuinely solves it, at the cost of a hosted dependency.

Until then, treat manual unseal as part of the nuke-and-rebuild drill and time
it, so the cost is visible rather than a surprise.
