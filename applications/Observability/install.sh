helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo add grafana https://grafana.github.io/helm-charts
helm repo update

kubectl create namespace monitoring

helm install prometheus-stack prometheus-community/kube-prometheus-stack \
--namespace monitoring \
--create-namespace -f values.yaml \
--hide-notes 

echo "--------------- Passwords ---------------"

echo "Grafana"
echo "Username: admin"
echo "Password: $(kubectl --namespace monitoring get secrets prometheus-stack-grafana -o jsonpath="{.data.admin-password}" | base64 -d ; echo) \n"
