#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

helm repo add argo https://argoproj.github.io/argo-helm
helm repo update argo

helm upgrade --install argocd argo/argo-cd \
  --namespace argocd \
  --create-namespace \
  -f values.yaml \
  --wait

kubectl apply -f root-apps.yaml

echo "ArgoCD installed. Initial admin password:"
kubectl -n argocd get secret argocd-initial-admin-secret \
  -o jsonpath='{.data.password}' | base64 -d
echo
