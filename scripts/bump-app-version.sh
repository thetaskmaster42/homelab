#!/bin/bash
# Bump the pinned image tag of one or more applications in apps/.
#
#   ./scripts/bump-app-version.sh rh-dashboard 1.0.3
#   ./scripts/bump-app-version.sh rh-dashboard 1.0.3 excalidraw 2026.09.01
#   ./scripts/bump-app-version.sh --check rh-dashboard
#
# This does NOT deploy. Pushing to main is the deploy, because ArgoCD reconciles
# from git -- so this leaves a reviewable diff and stops. Commit when you agree
# with it.
#
# The checks below are not ceremony. Every one of them corresponds to a way this
# has actually gone wrong:
#
#   * a `v` prefix that belongs on the git tag but not the image tag
#   * a tag that was never published, because merging a PR does not build an
#     image -- the release workflow fires on the TAG push
#   * an amd64-only image, which cannot schedule on this all-arm64 cluster
#   * an overlay whose manifests are sha-pinned separately from the image tag,
#     where bumping one without the other runs new code against old manifests
set -euo pipefail
cd "$(dirname "$0")/.."

CHECK_ONLY=false
[ "${1:-}" = "--check" ] && { CHECK_ONLY=true; shift; }

if [ $# -eq 0 ] || { [ "$CHECK_ONLY" = false ] && [ $(($# % 2)) -ne 0 ]; }; then
  sed -n '2,10p' "$0" | sed 's/^# \?//'
  exit 2
fi

red()  { printf '\033[31m%s\033[0m\n' "$*" >&2; }
warn() { printf '\033[33m%s\033[0m\n' "$*" >&2; }
ok()   { printf '\033[32m%s\033[0m\n' "$*"; }

# Read the pinned image name and current tag out of a kustomization without a
# YAML round-trip -- these files carry the reasoning for every pin, and
# safe_load/dump would silently delete every comment in them.
read_image() {
  python3 - "$1" <<'PY'
import re, sys
text = open(sys.argv[1]).read()
m = re.search(r'^\s*-\s*name:\s*(\S+)\s*$\n(?:^\s*#.*$\n)*^\s*newTag:\s*"?([^"\s]+)"?\s*$',
              text, re.MULTILINE)
print(f"{m.group(1)}\t{m.group(2)}" if m else "")
PY
}

# Replace ONLY the tag value, leaving every comment and byte of structure alone.
write_tag() {
  python3 - "$1" "$2" "$3" <<'PY'
import re, sys
path, image, new = sys.argv[1], sys.argv[2], sys.argv[3]
text = open(path).read()
pat = re.compile(r'(^\s*-\s*name:\s*' + re.escape(image) + r'\s*$\n(?:^\s*#.*$\n)*^\s*newTag:\s*")([^"]+)(")',
                 re.MULTILINE)
new_text, n = pat.subn(lambda m: m.group(1) + new + m.group(3), text, count=1)
if n != 1:
    sys.exit(f"could not locate the newTag for {image} in {path}")
open(path, "w").write(new_text)
PY
}

# A manifest LIST enumerates its platforms inline. A single manifest does not --
# its architecture lives in the config blob, so `inspect` alone reports nothing
# and the image looks mysteriously arch-less rather than plainly amd64-only.
# --verbose surfaces it, which is how tests/test_images_arm64.py does the same job.
arches_of() {
  local out
  out=$(docker manifest inspect "$1" 2>/dev/null | python3 -c '
import json,sys
try: d=json.load(sys.stdin)
except Exception: sys.exit(1)
ms=d.get("manifests")
if not ms: sys.exit(1)
print(" ".join(sorted({m["platform"]["architecture"] for m in ms
    if m.get("platform",{}).get("architecture") not in (None,"unknown")})))
' 2>/dev/null) && [ -n "$out" ] && { echo "$out"; return; }

  docker manifest inspect --verbose "$1" 2>/dev/null | python3 -c '
import json,sys
try: d=json.load(sys.stdin)
except Exception: sys.exit(1)
es = d if isinstance(d,list) else [d]
a = sorted({(e.get("Descriptor") or {}).get("platform",{}).get("architecture")
            for e in es} - {None,"unknown"})
print(" ".join(a))
' 2>/dev/null || true
}

changed=()
failed=0

while [ $# -gt 0 ]; do
  app="$1"; shift
  if [ "$CHECK_ONLY" = true ]; then version=""; else version="$1"; shift; fi

  kust="apps/$app/kustomization.yaml"
  echo
  echo "=== $app ==="
  if [ ! -f "$kust" ]; then
    red "  no such app: $kust does not exist"; failed=1; continue
  fi

  IFS=$'\t' read -r image current <<<"$(read_image "$kust")"
  if [ -z "${image:-}" ]; then
    red "  no pinned image found in $kust"; failed=1; continue
  fi
  echo "  image:   $image"
  echo "  current: $current"

  if [ "$CHECK_ONLY" = true ]; then
    arches=$(arches_of "$image:$current")
    echo "  arches:  ${arches:-<could not inspect>}"
    continue
  fi

  # A git tag of v1.0.3 publishes an IMAGE tag of 1.0.3: release.yml derives it
  # with ${GITHUB_REF_NAME#v}. Pinning the v here resolves to nothing and the
  # pod sits in ImagePullBackOff with no hint as to why.
  if [[ "$version" == v[0-9]* ]]; then
    warn "  note: stripping the leading 'v' -- git tag $version publishes image tag ${version#v}"
    version="${version#v}"
  fi
  echo "  target:  $version"

  if [ "$current" = "$version" ]; then
    ok "  already at $version, nothing to do"; continue
  fi

  # An overlay that pins its manifests to a commit sha must move both together.
  # Otherwise the image comes from one commit and the Deployment/Job/Cluster
  # manifests from another -- which is how a migration ends up running against
  # a schema its own build never saw.
  if grep -qE 'https://raw\.githubusercontent\.com/.*/[0-9a-f]{40}/' "$kust"; then
    red "  REFUSING: $app pins remote manifests to a commit sha as well as an image tag."
    red "            Bumping the tag alone would run new code against old manifests."
    red "            Update both by hand -- see the comment in $kust."
    failed=1; continue
  fi

  # Does the tag actually exist, and can it run here? Merging a PR does not
  # publish anything; the release workflow fires on the tag push.
  arches=$(arches_of "$image:$version")
  if [ -z "$arches" ]; then
    red "  REFUSING: $image:$version not found in the registry."
    red "            Was the git tag pushed, and did the release workflow finish?"
    failed=1; continue
  fi
  if [[ " $arches " != *" arm64 "* ]]; then
    red "  REFUSING: $image:$version has no linux/arm64 manifest (found: $arches)."
    red "            Every node in this cluster is arm64; it would CrashLoopBackOff."
    failed=1; continue
  fi
  ok "  registry: $version exists, arches = $arches"

  write_tag "$kust" "$image" "$version"

  if ! kubectl kustomize "apps/$app" >/dev/null 2>&1; then
    red "  render FAILED after the edit -- reverting"
    git checkout -- "$kust"; failed=1; continue
  fi
  ok "  rendered clean"
  changed+=("$app")
done

echo
if [ ${#changed[@]} -gt 0 ]; then
  echo "=== diff ==="
  git --no-pager diff -- $(printf 'apps/%s/kustomization.yaml ' "${changed[@]}")
  echo
  ok "Updated: ${changed[*]}"
  echo "Next:  make validate  &&  git commit  &&  git push    # the push IS the deploy"
fi

exit $failed
