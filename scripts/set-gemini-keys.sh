#!/usr/bin/env bash
# Store a Google account name and one or more Gemini API keys in a Kubernetes Secret, without any of it ever
# touching disk.
#
#   scripts/set-gemini-keys.sh [-n NAMESPACE] [-s SECRET] [--no-verify] [--restart]
#
# You are asked first for the Google account the keys belong to (for example the account you were given for a
# hackathon), then for each key with hidden input (nothing is echoed). Everything lives only in this shell's
# memory and travels to kubectl through pipes. Stored as GEMINI_ACCOUNT, GEMINI_API_KEY_1, GEMINI_API_KEY_2, ...
# (plus GEMINI_API_KEY, a copy of the first key, for single-key setups). An existing Secret of that name is
# replaced. Nothing is written to a file, placed on a command line, or kept in a last-applied annotation; of a key
# only a short fingerprint (the first 8 hex digits of its SHA-256) is shown so you can tell keys apart.
#
# The account name is a label, not a credential: the API keys are what authenticate. It is kept inside the Secret
# (not in a label or annotation) and Murray shows it only to an operator, next to the usage and cost figures, so
# it is always clear whose quota is being spent.
#
# Defaults: NAMESPACE from k8s.local/cluster.env (MURRAY_NAMESPACE) if present, else "murray"; SECRET "murray-gemini".
# Unless --no-verify is given, each key is checked against the Gemini API (a models list call; the key goes only to
# Google, in a request header read from a pipe). --restart restarts the app Deployment afterwards so it loads them.
set -uo pipefail
set +x

ROOT="$(dirname "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")")"
NS=""
SECRET="murray-gemini"
VERIFY=yes
RESTART=no
while [[ $# -gt 0 ]]; do
  case "$1" in
    -n) NS="${2:?-n needs a namespace}"; shift 2 ;;
    -s) SECRET="${2:?-s needs a secret name}"; shift 2 ;;
    --no-verify) VERIFY=no; shift ;;
    --restart) RESTART=yes; shift ;;
    -h|--help) sed -n '2,21p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
if [[ -z "$NS" ]]; then
  if [[ -f "$ROOT/k8s.local/cluster.env" ]]; then
    NS="$(sed -n 's/^MURRAY_NAMESPACE=//p' "$ROOT/k8s.local/cluster.env" | head -1)"
  fi
  NS="${NS:-murray}"
fi
for c in kubectl sha256sum; do command -v "$c" >/dev/null 2>&1 || { echo "missing command: $c" >&2; exit 1; }; done
[[ -t 0 ]] || { echo "run this in an interactive terminal: it asks for the keys with hidden input" >&2; exit 1; }
kubectl get namespace "$NS" >/dev/null 2>&1 || { echo "namespace '$NS' does not exist (kubectl context: $(kubectl config current-context 2>/dev/null))" >&2; exit 1; }

KEYS=()
ACCOUNT=""
cleanup() { for i in "${!KEYS[@]}"; do KEYS[$i]=""; done; unset KEYS key manifest ACCOUNT; }
trap cleanup EXIT

echo "Gemini credentials for Secret '$SECRET' in namespace '$NS'."
while [[ -z "$ACCOUNT" ]]; do
  IFS= read -r -p "Google account these keys belong to (its email address or name): " ACCOUNT || true
  ACCOUNT="$(printf %s "$ACCOUNT" | tr -d '\r\n' | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
  if [[ ${#ACCOUNT} -gt 120 || "$ACCOUNT" =~ ^AIza ]]; then
    echo "  that looks like a key, not an account name; nothing stored. Try again." >&2
    ACCOUNT=""
  fi
done
echo "Now the API keys of that account."
echo "Paste each key and press Enter (input is hidden). Press Enter on an empty line when you have no more keys."
while :; do
  n=$(( ${#KEYS[@]} + 1 ))
  key=""
  IFS= read -rs -p "  key #$n: " key || true
  echo
  key="${key//[[:space:]]/}"
  [[ -z "$key" ]] && break
  if [[ ! "$key" =~ ^[A-Za-z0-9_-]{30,80}$ ]]; then
    echo "  that does not look like an API key (length ${#key}); not stored. Try again." >&2
    continue
  fi
  fp="$(printf %s "$key" | sha256sum | cut -c1-8)"
  dup=no
  for k in "${KEYS[@]:-}"; do [[ -n "$k" && "$k" == "$key" ]] && dup=yes; done
  if [[ "$dup" == yes ]]; then echo "  (same key as an earlier one, skipped)"; continue; fi
  status="not verified"
  if [[ "$VERIFY" == yes ]] && command -v curl >/dev/null 2>&1; then
    # the header is read from a pipe, so the key is never on a command line
    body="$(printf 'x-goog-api-key: %s\n' "$key" | curl -sS -m 25 -H @- 'https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000' 2>&1)" || body=""
    if printf %s "$body" | grep -q '"models"'; then
      seen=0
      for m in gemini-4-hydrogen gemini-4-neon gemini-4-argon gemini-4-carbon; do
        printf %s "$body" | grep -q "models/$m\"" && seen=$((seen + 1))
      done
      status="accepted by the Gemini API; lists $seen of the 4 Gemini 4 models"
    else
      status="REJECTED by the Gemini API ($(printf %s "$body" | grep -o '"message": *"[^"]*"' | head -1 | cut -c1-120))"
      echo "  key #$n  fingerprint $fp  length ${#key}: $status" >&2
      read -r -p "  store it anyway? [y/N] " yn
      [[ "$yn" == [yY]* ]] || continue
    fi
    unset body
  fi
  KEYS+=("$key")
  echo "  key #$n  fingerprint $fp  length ${#key}: $status"
done
[[ ${#KEYS[@]} -gt 0 ]] || { echo "no keys entered; nothing changed." >&2; exit 1; }

# Build the Secret in memory: each value reaches kubectl through its own pipe (/dev/fd/N), never through a file.
args=()
fds=()
exec {fd}< <(printf %s "$ACCOUNT")
fds+=("$fd"); args+=("--from-file=GEMINI_ACCOUNT=/dev/fd/$fd")
exec {fd}< <(printf %s "${KEYS[0]}")
fds+=("$fd"); args+=("--from-file=GEMINI_API_KEY=/dev/fd/$fd")
i=0
for key in "${KEYS[@]}"; do
  i=$((i + 1))
  exec {fd}< <(printf %s "$key")
  fds+=("$fd"); args+=("--from-file=GEMINI_API_KEY_$i=/dev/fd/$fd")
done
manifest="$(kubectl -n "$NS" create secret generic "$SECRET" "${args[@]}" --dry-run=client -o json)" || { echo "could not build the Secret" >&2; exit 1; }
for fd in "${fds[@]}"; do exec {fd}<&-; done
if kubectl -n "$NS" get secret "$SECRET" -o name >/dev/null 2>&1; then
  printf %s "$manifest" | kubectl -n "$NS" replace -f - >/dev/null || { echo "replace failed; the old Secret is unchanged" >&2; exit 1; }
  verb="replaced"
else
  printf %s "$manifest" | kubectl -n "$NS" create -f - >/dev/null || { echo "create failed" >&2; exit 1; }
  verb="created"
fi
kubectl -n "$NS" label secret "$SECRET" app.kubernetes.io/part-of=murray --overwrite >/dev/null 2>&1 || true
echo
echo "Secret '$SECRET' $verb in namespace '$NS': account '$ACCOUNT', ${#KEYS[@]} key(s)."
kubectl -n "$NS" describe secret "$SECRET" | sed -n '/^Data/,$p'
if [[ "$RESTART" == yes ]]; then
  kubectl -n "$NS" rollout restart deploy/murray && kubectl -n "$NS" rollout status deploy/murray --timeout=180s
else
  echo "The running app keeps its old keys until it restarts:  kubectl -n $NS rollout restart deploy/murray"
fi
