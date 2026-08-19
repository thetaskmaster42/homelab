# Headlamp

The cluster resource browser, at <https://headlamp.mongoose-galaxy.ts.net>.

Reachable only over the tailnet. It must never be funneled: the chart binds its
ServiceAccount to `cluster-admin`, so the tailnet is the only thing standing
between the internet and full control of the cluster.

## Getting a login token

Headlamp authenticates with a Kubernetes ServiceAccount token. Mint one:

```sh
kubectl create token headlamp -n headlamp --duration=24h
```

Paste it into the token prompt. That is the whole flow — there is no password
and no account to create.

`--duration` is optional and defaults to one hour. Twenty-four is a reasonable
working day; the cluster accepts it. Do not reach for a very long expiry to avoid
re-running the command: this token *is* cluster-admin, and a short life is the
main thing limiting the damage if it is captured from a clipboard, a shell
history, or a screenshot.

The token is not stored anywhere and cannot be retrieved again — mint a fresh one
whenever it expires.

## Why cluster-admin

`infra/services/headlamp/values.yaml` sets
`clusterRoleBinding.clusterRoleName: cluster-admin`, because the point of this
pane is to inspect and occasionally fix things. The access control that matters
is the tailnet in front of it, not the ServiceAccount behind it.

If Headlamp ever becomes reachable more widely, change that binding to a
read-only ClusterRole first:

```yaml
clusterRoleBinding:
  clusterRoleName: view
```

## What it is for

Live resource state — nodes, pods, ConfigMaps, CRDs, logs, events. It is one of
three panes and deliberately not the only one:

| Pane | Answers |
|---|---|
| Headlamp | what exists right now, and its state |
| ArgoCD UI | what git says should exist, and whether reality matches |
| Grafana | how it has behaved over time |

Headlamp shows drift as it *is*; ArgoCD shows drift as a *diff against git*.
Reaching for the wrong one is the usual reason a problem looks mysterious.
