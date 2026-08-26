# 0009 — Fork Excalidraw to get an arm64 image we are allowed to pin

**Status:** accepted, 2026-08-26
**Follows:** [ADR 0007](0007-drawio-diagramming.md), which chose draw.io and
rejected Excalidraw on exactly the problem this ADR solves.

## Context

ADR 0007 rejected Excalidraw because of the arm64 gate. That rejection was
correct on the evidence, and the evidence has not changed:

| Image | Tags | arm64 on a pinned tag |
|---|---|---|
| `excalidraw/excalidraw` | 978 | **only `latest`** |
| `excalidraw/excalidraw-room` | 48 | **none**; last build Dec 2023 |

The cause turned out to be a publishing choice rather than a build limitation.
Upstream's `publish-docker.yml` *does* build `linux/amd64, linux/arm64,
linux/arm/v7` — but it triggers only on pushes to a `release` branch and tags
only `latest`. So arm64 exists and is simply never pinned to a version.

That leaves one arm64 artifact, and it is the floating tag `CLAUDE.md` bans and
`tests/test_apps.py::test_image_tags_are_not_floating` rejects.

## Decision

Fork to `thetaskmaster42/excalidraw` and publish the image ourselves, using the
**unmodified upstream Dockerfile**. The fork carries exactly one added file,
`.github/workflows/homelab-arm64.yml`; `publish-docker.yml` is untouched.

No Dockerfile changes were needed, which is worth recording because it was not
obvious in advance:

- It is already multi-arch aware — `FROM --platform=${BUILDPLATFORM}` with
  `npm_config_target_arch=${TARGETARCH}`, so the heavy Vite build runs natively
  on the build platform rather than under emulation.
- Both base images are **digest-pinned**, which is where this could have failed:
  a digest pointing at an amd64-only manifest would break an arm64 build
  outright. Both were checked first and both resolve to manifest indexes
  containing arm64 (`node:24`, `nginx:stable-alpine-slim`).

The workflow builds on `ubuntu-24.04-arm` — native arm64, free for public
repositories — rather than `ubuntu-latest` with QEMU. `docs/adding-application.md`
already prescribes this, and the result bears it out: **3.9 minutes** for a full
Vite build of a large monorepo.

### Versioning

Published as a **date snapshot**, `2026.08.26`, plus a `sha-<short>` tag.

The fork tracks upstream `master`, which is **389 commits ahead of `v0.18.1`**.
Tagging it `0.18.1` would be false. A semver prerelease like `0.18.1-master` is
worse than false: prereleases sort *before* their base version, so it would claim
to be older than a release it is 389 commits newer than. A date claims only what
is true — this is the tree as of that day — and the `sha-` tag carries exact
provenance across resyncs. The workflow also accepts `v*` tags if upstream-style
versioning is wanted later.

## Verification

Not inferred from a green check. The published image was run on the cluster:

| | |
|---|---|
| Architecture | `linux/arm64` (anonymous pull, so no imagePullSecret needed) |
| Image size | **29 MB** (draw.io is 405 MB) |
| Pull on a Pi | 3.4s |
| Start → serving | <1s |
| Response | `HTTP 200`, `<title>Excalidraw Whiteboard</title>`, TTFB 8ms |
| Restarts | 0 — no `exec format error`, so arm64 is right in practice |
| Idle | **4 MiB RSS, 1m CPU** |

## Consequences

- **Two whiteboards now run.** draw.io and Excalidraw overlap, deliberately:
  they are different tools (draw.io for structured diagrams, Excalidraw for
  freehand sketching) and both are cheap. Excalidraw is the cheaper of the two
  by an order of magnitude — 29 MB and 4 MiB idle against 405 MB and 90 MiB.
- **Not `runAsNonRoot`.** The image declares no `USER`, so nginx's master starts
  as root to bind `:80`. Forcing a non-root UID makes the entrypoint fail rather
  than degrade. Instead every capability is dropped and five added back
  (`NET_BIND_SERVICE`, `CHOWN`, `SETUID`, `SETGID`, `DAC_OVERRIDE`), verified by
  running the image with exactly that set before committing. Running fully
  unprivileged would mean overriding `nginx.conf` to a high port via a ConfigMap
  — available if wanted, not taken for a static file server on a private tailnet.
- **No collaboration.** `excalidraw-room` still has no arm64 build and has not
  been rebuilt since 2023. Shared sessions would need the same fork-and-build
  treatment against a second repository.
- **No server-side persistence.** Drawings live in browser `localStorage`, so
  they are per-device and lost when site data is cleared. Building the image
  changes nothing about this — it is how Excalidraw works.
- **Upstream resyncs are manual**, and each one needs a rebuild and a tag bump
  here. That is the cost of pinning, and it is the point of pinning.
