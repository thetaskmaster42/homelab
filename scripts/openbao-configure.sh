#!/bin/bash
# One-time OpenBao setup, plus per-application roles.
#
#   read -rs BAO_TOKEN && export BAO_TOKEN      # paste the root token, no echo
#   ./scripts/openbao-configure.sh platform
#   ./scripts/openbao-configure.sh app docmost docmost docmost
#   unset BAO_TOKEN
#
# The token is read from the environment and passed to the pod over STDIN, never
# as an argument: anything in argv is visible in `ps` inside the container for
# as long as the command runs, and ends up in shell history on the way in.
#
# Everything here is idempotent. Re-running is how you converge a rebuilt
# cluster, and `homelab nuke` destroys OpenBao's volume (it is on local-path by
# ADR 0008), so re-running is the expected case rather than the exception.
set -euo pipefail
cd "$(dirname "$0")/.."

NS=openbao
POD=openbao-0

die()  { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }
ok()   { printf '\033[32m%s\033[0m\n' "$*"; }

[ -n "${BAO_TOKEN:-}" ] || die "BAO_TOKEN is not set. Use: read -rs BAO_TOKEN && export BAO_TOKEN"

# Run a script inside the pod with BAO_TOKEN supplied on stdin.
#
# The script goes in ARGV and the token on STDIN, and that split is the whole
# point. `sh -s` reads its *script* from stdin, so piping the token there too
# made the first line execute as a command -- the token itself became a failed
# `sh: <token>: not found`, BAO_TOKEN stayed empty, and every call came back
# 403. The script is not secret; the token is, and argv is visible in `ps`
# inside the container.
#
# $1 is the script text; further arguments are passed through as $2.. to it.
bao_exec() {
  local script="$1"; shift
  printf '%s\n' "$BAO_TOKEN" | kubectl -n "$NS" exec -i "$POD" -- sh -c '
read -r BAO_TOKEN
export BAO_TOKEN
export BAO_ADDR="${BAO_ADDR:-http://127.0.0.1:8200}"
script="$1"; shift
eval "$script"
' _ "$script" "$@"
}

preflight() {
  local status
  status=$(kubectl -n "$NS" exec "$POD" -- bao status 2>&1 || true)
  grep -q "Initialized *true"  <<<"$status" || die "OpenBao is not initialised. Run: bao operator init -key-shares=3 -key-threshold=2"
  grep -q "Sealed *false"      <<<"$status" || die "OpenBao is SEALED. Unseal with 2 of 3 keys before configuring:
  kubectl -n $NS exec -it $POD -- bao operator unseal    # twice, different keys"
}

platform() {
  preflight
  bao_exec "$(cat <<'REMOTE'
set -e

# Audit devices are NOT enabled here, and cannot be: OpenBao 2.x refuses to
# create them through the API ("use declarative, config-based audit device
# management instead"). The stanza lives in infra/services/openbao/values.yaml
# and is applied at start-up. This only reports what is actually active, because
# an unaudited secret store that looks configured is worse than one that
# obviously is not.
if bao audit list 2>/dev/null | grep -q .; then
  echo "audit: active -> $(bao audit list -format=json 2>/dev/null | head -c 120)"
else
  echo "audit: NONE ACTIVE -- check the audit stanza in the openbao values"
fi

# KV v2 for versioning and soft-delete: a bad write is recoverable rather than
# terminal.
if ! bao secrets list -format=json 2>/dev/null | grep -q '"secret/"'; then
  bao secrets enable -path=secret -version=2 kv
  echo "kv: enabled secret/ (v2)"
else
  echo "kv: secret/ already enabled"
fi

# Kubernetes auth: workloads authenticate with the ServiceAccount token the
# kubelet already projects for them, so no credential is ever stored in git, in
# a Secret, or in the image. The chart already binds this pod's SA to
# system:auth-delegator, which is what lets OpenBao call TokenReview.
if ! bao auth list -format=json 2>/dev/null | grep -q '"kubernetes/"'; then
  bao auth enable kubernetes
  echo "auth: enabled kubernetes"
fi
bao write auth/kubernetes/config \
  kubernetes_host="https://${KUBERNETES_SERVICE_HOST}:${KUBERNETES_SERVICE_PORT}" >/dev/null
echo "auth: kubernetes configured -> https://${KUBERNETES_SERVICE_HOST}:${KUBERNETES_SERVICE_PORT}"

# An admin identity that is not root, so the root token can be revoked. Root
# should be generated on demand with `bao operator generate-root` on the rare
# occasions it is genuinely needed, not left lying in a password manager.
bao policy write admin - >/dev/null <<'POLICY'
path "*" {
  capabilities = ["create", "read", "update", "delete", "list", "sudo"]
}
POLICY
echo "policy: admin written"
REMOTE
)"
  ok "platform configured"
  cat <<'NEXT'

Next, and only once you have an admin login that works:

  # create a non-root admin (userpass), then verify you can log in with it
  kubectl -n openbao exec -it openbao-0 -- bao auth enable userpass
  kubectl -n openbao exec -it openbao-0 -- sh -c \
    'bao write auth/userpass/users/admin password=- policies=admin'

  # only then revoke root -- verify the replacement FIRST
  kubectl -n openbao exec -it openbao-0 -- bao token revoke -self

NEXT
}

# app <name> <namespace> <serviceaccount>
app() {
  [ $# -eq 3 ] || die "usage: $0 app <name> <namespace> <serviceaccount>
  e.g. $0 app docmost docmost docmost"
  local name="$1" ns="$2" sa="$3"
  preflight
  bao_exec "$(cat <<'REMOTE'
set -e
NAME="$1"; NS="$2"; SA="$3"

# NOTE THE PATH. KV v2 rewrites reads to secret/data/<path>, so a policy written
# against secret/<path> silently matches nothing and every read is denied with
# no hint as to why. This is the single most common mistake with KV v2.
bao policy write "$NAME" - >/dev/null <<POLICY
path "secret/data/$NAME/*" {
  capabilities = ["read"]
}
path "secret/metadata/$NAME/*" {
  capabilities = ["list"]
}
POLICY

# bound_service_account_names is never "*": that would let any pod in the
# namespace assume this role, which is most of the point of using the
# ServiceAccount as the identity in the first place.
bao write "auth/kubernetes/role/$NAME" \
  bound_service_account_names="$SA" \
  bound_service_account_namespaces="$NS" \
  token_policies="$NAME" \
  token_ttl=1h >/dev/null

echo "app '$NAME': policy + role bound to $NS/$SA, read on secret/$NAME/*"
REMOTE
)" "$name" "$ns" "$sa"
  ok "role created"
  echo "  seed with:  kubectl -n $NS exec -it $POD -- bao kv put secret/$1/config key=value"
}

case "${1:-}" in
  platform) platform ;;
  app)      shift; app "$@" ;;
  *) sed -n '2,10p' "$0" | sed 's/^# \?//'; exit 2 ;;
esac
