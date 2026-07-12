# Archive

Content from the pre-v2 repo, kept for reference only — nothing here is
maintained or expected to work as-is.

| Directory | What it was |
|---|---|
| `Observability/` | kube-prometheus-stack helm install + values |
| `airflow/` | Airflow helm install one-liner |
| `catalog/` | Hive metastore systemd unit, Apache Polaris setup |
| `spark/` | Spark cluster test/diagnosis scripts |
| `voting-app/` | Example k8s app (Deployment and raw-Pod variants) |
| `k3s-v1/` | Old k3s install/uninstall scripts (old `.lan` topology) |
| `nuke-all.sh`, `start-resources.sh` | Old machine-specific helper scripts |

When a service from here is redeployed, it gets rebuilt properly under
`cluster/` and the archived copy can eventually be deleted.
