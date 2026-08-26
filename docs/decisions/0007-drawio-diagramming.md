# 0007 — draw.io for diagramming, client-side only

**Status:** accepted, 2026-08-26

## Context

The cluster needed a diagramming tool. Two candidates were considered seriously,
draw.io and Excalidraw, and the deciding constraint was the one that decides most
things here: **every node is arm64**.

## The arm64 survey

Measured against the registries, not from documentation:

| Image | Total tags | arm64 on a *pinned* tag | Notes |
|---|---|---|---|
| `jgraph/drawio` | 939 | ✅ `31.3.2`, `31.3.1`, `31.1.8`, … | released 2026-08-24, actively built |
| `excalidraw/excalidraw` | 978 | ❌ **arm64 only on `latest`** | every pinned tag is a `sha-*` from 2021, amd64-only |
| `excalidraw/excalidraw-room` | 48 | ❌ **none** | last build Dec 2023 |
| `jgraph/export-server` | 6 | ❌ **none** | actively built — for amd64 only |
| `linuxserver/drawio` | — | — | does not exist |
| `yuzutech/kroki` | 60 | ✅ `0.32.1` | text→diagram, not an interactive canvas |
| `plantuml/plantuml-server` | 193 | ✅ `v1.2026.6` | same category as kroki |

Excalidraw was rejected on this alone. Using it would mean either pinning
`latest` — banned by `CLAUDE.md` and enforced by
`tests/test_apps.py::test_image_tags_are_not_floating` — or building our own
arm64 image. Its collaboration server has no arm64 build at all and has not been
rebuilt since 2023, so shared editing was never on the table.

Kroki and PlantUML are complements, not substitutes: both render diagrams from
text and neither offers a canvas.

## Decision

Deploy `jgraph/drawio:31.3.2` as a stateless kustomize app, tailnet-only.

## Storage: none, and why that is not a configuration gap

**draw.io has no server-side save path.** The container serves static JavaScript;
the editor runs in the browser and saves from there. The server never receives a
diagram. Mounting a PVC would create a volume nothing writes to — there is no
`SAVE_PATH` setting because the feature does not exist.

Its supported destinations are the device, browser `localStorage`, and OAuth
integrations. GitHub was the preferred target and **is not available**:

```sh
# main/docker-entrypoint.sh, line 96
echo "urlParams['gh'] = '0'; //github" >> .../js/PreConfig.js
```

Hardcoded off, unconditionally. The full env-var surface is GitLab, Google and
MSGraph — there is no `DRAWIO_GITHUB_*` anything. The client *would* read it
(`GitHubClient.prototype.clientId = … : window.DRAWIO_GITHUB_ID`) but nothing
sets that global, and completing GitHub's OAuth needs a server-side token
exchange holding the client secret, which the container wires up for GitLab and
Google only.

So the options were: switch to **GitLab** (fully supported, `DRAWIO_GITLAB_ID` /
`_SECRET` / `_URL`, secret via SOPS), embed in **Nextcloud** (upstream ships a
`nextcloud/` compose setup for exactly this), or save locally and commit by hand.

**For this MVP: save locally, push to GitHub manually.** No OAuth app, no secret
in the bootstrap bundle, no PVC. Nextcloud is the intended path if this outgrows
the MVP; GitLab remains available if native integration matters more than which
forge is used.

The upside is real and worth naming: with no volume, this is the only stateful-
feeling app in the cluster with **no dependency on `portal`**. It keeps working
through a NAS outage that blocks Postgres, Prometheus and OpenBao (see
[ADR 0006](0006-nfs-default-storage.md)).

The downside is equally real: diagrams live in one browser on one device.
Nothing syncs, and clearing site data loses unsaved work.

## Measurements

Taken from a throwaway pod on `k3s-worker-2`, not estimated:

| | Cold | Warm |
|---|---|---|
| Image pull (405 MB) | 14.5s | — |
| Container start → serving | **5.0s** | **6.0s** |
| Scheduled → Ready | ~20s | ~6s |

Idle: **~90 MiB RSS, 8m CPU**. First response `HTTP 200`, TTFB 8ms.

Two planning assumptions were wrong and are corrected in the manifests: JVM cold
start was expected to run to "tens of seconds" and is 5–6s, and the first draft
requested 512Mi/1Gi where ~90Mi idle makes 128Mi/512Mi correct. Under JDK 11
container awareness the heap tracks the memory *limit*, so an inflated limit
inflates the heap for nothing.

## Consequences

- **No server-side export.** `jgraph/export-server` is amd64-only, so exports
  render in the browser. Interactive use is unaffected; server-rendered export
  via URL/API is not available.
- **`runAsGroup: 0` is required.** `CATALINA_HOME` is chowned `tomcat:0` and the
  entrypoint writes `PreConfig.js` at startup. When that write fails it does not
  crash — it warns and starts anyway with every `DRAWIO_*` setting skipped,
  **including the CSP header**. The failure mode is a working-looking app that
  quietly lost a security control, which is why `readOnlyRootFilesystem` is also
  false here.
- **`ENABLE_DRAWIO_PROXY` stays off.** It fetches arbitrary URLs server-side —
  an SSRF primitive aimed at `portal` and the Gateway Pi, reachable by anyone on
  the tailnet.
- **No authentication whatsoever.** The tailnet is the only control. Never
  funnel this.
- **405 MB image**, pulled once per node onto node-local ephemeral storage.
  The heaviest image in the cluster; unrelated to the NFS default.
