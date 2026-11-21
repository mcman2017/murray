#!/usr/bin/env bash
# Render k8s/base/*.yaml with the values in an env file into an output directory.
#   scripts/render-manifests.sh [ENV_FILE] [OUT_DIR]     (defaults: k8s.local/cluster.env, k8s.local/rendered)
# Nothing is applied: review the output, then `kubectl apply -f <OUT_DIR>/<file>`.
set -euo pipefail
ROOT="$(dirname "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")")"
ENV_FILE="${1:-$ROOT/k8s.local/cluster.env}"
OUT_DIR="${2:-$ROOT/k8s.local/rendered}"
[[ -f "$ENV_FILE" ]] || { echo "missing $ENV_FILE (start from k8s/cluster.env.example)" >&2; exit 1; }
command -v envsubst >/dev/null || { echo "envsubst is required (gettext)" >&2; exit 1; }
set -a; . "$ENV_FILE"; set +a
VARS='${MURRAY_NAMESPACE} ${MURRAY_IMAGE} ${MURRAY_HOST} ${STORAGE_CLASS} ${CLUSTER_ISSUER} ${PULL_SECRET} ${INGRESS_NAMESPACE} ${MURRAY_AUTOSTART}'
for v in MURRAY_NAMESPACE MURRAY_IMAGE MURRAY_HOST STORAGE_CLASS CLUSTER_ISSUER PULL_SECRET INGRESS_NAMESPACE MURRAY_AUTOSTART; do
  [[ -n "${!v:-}" ]] || { echo "$v is not set in $ENV_FILE" >&2; exit 1; }
done
mkdir -p "$OUT_DIR"
for f in "$ROOT"/k8s/base/*.yaml; do
  envsubst "$VARS" < "$f" > "$OUT_DIR/$(basename "$f")"
done
if grep -n '\${' "$OUT_DIR"/*.yaml; then echo "unrendered placeholders remain" >&2; exit 1; fi
echo "rendered $(ls "$OUT_DIR" | wc -l) file(s) into $OUT_DIR"
