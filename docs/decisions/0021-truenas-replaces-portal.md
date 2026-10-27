# 0021: TrueNAS replaces `portal` as the NAS, one app at a time

## Status

Accepted — phases 1–3 done for memos (2026-10-27): `truenas` StorageClass live,
barman archiving to Garage, and `memos-pg` recovered from that archive onto
`truenas`, then cut over; the `nfs`-backed `memos-db` is deleted. Next: phase 4,
plain data volumes (`memos-data` is still on `nfs`).

## Context

`portal` is a 4 GB Raspberry Pi running OpenMediaVault, and it holds every byte
of application and database state. It has no redundancy and no backup target.
A TrueNAS SCALE host is now available at 192.168.11.4 with a ZFS pool (`tank`),
snapshots, and room for an S3 endpoint (Garage) to receive CloudNativePG
backups.

A provisioned PersistentVolume's `spec.nfs.server` and `path` are immutable, so
"change the server in values.yaml" does not move data — it strands it (see
`docs/runbooks/nfs-path-change.md`).

## Decision

Run a second provisioner, `nfs-truenas`, exposing the StorageClass `truenas`,
alongside the existing `nfs`. Move workloads across individually:

1. **Provisioner** — this change. No consumers, not default.
2. **Backups** — barman-cloud plugin writing to Garage on TrueNAS.
3. **Postgres** — per app, a new `Cluster` on `truenas` *recovered from the
   barman backup*, which is also the first proof the backups restore.
4. **Plain data volumes** — scale to zero, copy, switch the claim.
5. **Default** — `truenas` becomes the default class; `nfs-provisioner` is removed.
6. **Retire `portal`** after a read-only grace period.

## Verified before this landed

- Kubelet mount from `k3s-worker-1` with `nfsvers=4.1,hard,timeo=600,retrans=2`.
- The export is limited to `192.168.11.0/24`.
- Files created as uid 26 (Postgres) are owned by 26:26. The first test showed
  everything owned by root — the share had **Mapall** set — which would have
  stopped Postgres starting; it is now Maproot-only.

## Consequences

- Two NFS servers are live during the migration; a stateful outage means
  checking both.
- The NFSv4 path is the full dataset path on SCALE, not a pseudo-root-relative
  one as on OMV.
- Backups and live data will share one TrueNAS host. ZFS snapshots protect
  against deletion and bad migrations, not against losing the box.
