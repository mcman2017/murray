#!/usr/bin/env bash
# Render the manifests and apply them (namespace, volume, network policies, sandbox, app). The public route is
# NOT applied here: review k8s.local/rendered/ingress.yaml and apply it yourself when you are ready to go live.
#   scripts/deploy.sh [ENV_FILE]            (default: k8s.local/cluster.env; start from k8s/cluster.env.example)
# Secrets are never created here. Before the first deploy, in the target namespace:
#   kubectl -n <ns> create secret generic murray-gemini --from-literal=GEMINI_API_KEY="$GEMINI_API_KEY"
#   openssl rand -hex 24 | tr -d '\n' | kubectl -n <ns> create secret generic murray-admin --from-file=MURRAY_ADMIN_TOKEN=/dev/stdin
set -euo pipefail
ROOT="$(dirname "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")")"
ENV_FILE="${1:-$ROOT/k8s.local/cluster.env}"
OUT="$ROOT/k8s.local/rendered"
"$ROOT/scripts/render-manifests.sh" "$ENV_FILE" "$OUT"
set -a; . "$ENV_FILE"; set +a
kubectl apply -f "$OUT/namespace.yaml"
for s in murray-gemini murray-admin; do
  kubectl -n "$MURRAY_NAMESPACE" get secret "$s" -o name >/dev/null 2>&1 || { echo "missing secret $s in namespace $MURRAY_NAMESPACE (see the header of this script)" >&2; exit 1; }
done
FILES=(-f "$OUT/pvc.yaml" -f "$OUT/networkpolicies.yaml" -f "$OUT/sandbox.yaml" -f "$OUT/app.yaml")
kubectl apply --dry-run=server "${FILES[@]}" >/dev/null
kubectl apply "${FILES[@]}"
kubectl -n "$MURRAY_NAMESPACE" rollout status deploy/murray-sandbox --timeout=300s
kubectl -n "$MURRAY_NAMESPACE" rollout status deploy/murray --timeout=300s
kubectl -n "$MURRAY_NAMESPACE" get pods -o wide
