#!/usr/bin/env bash
# Scan the files git would publish for secrets and private infrastructure details. Run before every push.
#   scripts/secret_scan.sh            scans tracked, staged and untracked-but-not-ignored files (outside a git repo: every file
#                                     except the private directories)
# Exit status 0 = clean. Generic patterns live here; names that are private to one cluster (node names, internal
# hostnames, usernames) go one per line in a git-ignored file, k8s.local/denylist.txt, so that this script itself
# gives nothing away.
set -uo pipefail
ROOT="$(dirname "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")")"
cd "$ROOT" || exit 2
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  # tracked, staged, and anything not ignored that a later `git add` could pick up
  mapfile -t FILES < <( { git ls-files; git diff --cached --name-only --diff-filter=ACM; git ls-files --others --exclude-standard; } | sort -u )
else
  mapfile -t FILES < <(find . -type f -not -path './_cluster_backup/*' -not -path './k8s.local/*' -not -path './.git/*' \
    -not -name '.env' -not -name '*.db*' -not -name 'initial-prompt.md' | sed 's#^\./##' | sort)
fi
[[ ${#FILES[@]} -gt 0 ]] || { echo "nothing to scan"; exit 0; }
fail=0
check() { # <label> <extended regex> [files to skip, as an extended regex]
  local label="$1" re="$2" skip="${3:-^$}" hits
  hits="$(printf '%s\n' "${FILES[@]}" | grep -v -E "$skip" | tr '\n' '\0' | xargs -0 -r grep -I -n -H -E -e "$re" 2>/dev/null | cut -c1-200)"
  if [[ -n "$hits" ]]; then echo "FOUND: $label"; echo "$hits" | sed 's/^/    /' | head -20; fail=1; fi
}
SELF='^scripts/secret_scan\.sh$'
check "Google API key"                   'AIza[0-9A-Za-z_-]{35}' "$SELF"
check "private key block"                'BEGIN [A-Z ]*PRIVATE KEY' "$SELF"
check "generic secret assignment"        '(api[_-]?key|secret|token|passwd|password)["'"'"' ]*[:=][ "'"'"']*[A-Za-z0-9_/+=-]{24,}' "$SELF|^tests/|^eval/test_set\.json$|^eval/build_test_set\.py$"
check "Slack / GitHub / AWS token"       '(xox[baprs]-[0-9A-Za-z-]{10,}|gh[pousr]_[0-9A-Za-z]{30,}|AKIA[0-9A-Z]{16})' "$SELF"
check "private IPv4 address"             '\b(10\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}|192\.168\.[0-9]{1,3}\.[0-9]{1,3}|172\.(1[6-9]|2[0-9]|3[01])\.[0-9]{1,3}\.[0-9]{1,3})\b' "$SELF|^k8s/base/networkpolicies\.yaml$"
check "kubeconfig material"              '(client-certificate-data|client-key-data|certificate-authority-data)' "$SELF"
check "e-mail address"                   '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}' "$SELF"
check "home directory path"              '/home/[a-z][a-z0-9_-]+' "$SELF"
if [[ -f k8s.local/denylist.txt ]]; then
  while IFS= read -r word; do
    [[ -z "$word" || "$word" == \#* ]] && continue
    hits="$(printf '%s\0' "${FILES[@]}" | xargs -0 -r grep -I -n -H -i -F -e "$word" 2>/dev/null | cut -c1-200)"
    # k8s.local/allowlist.txt may excuse one deliberate public mention: lines of the form  <file>:<exact text>
    if [[ -n "$hits" && -f k8s.local/allowlist.txt ]]; then
      while IFS= read -r allow; do
        [[ -z "$allow" || "$allow" == \#* ]] && continue
        afile="${allow%%:*}"; atext="${allow#*:}"
        hits="$(printf '%s\n' "$hits" | awk -v f="$afile" -v t="$atext" -v w="$word" 'BEGIN{IGNORECASE=1} { line=$0; if (index(line, f ":") == 1) { rest=line; while ((i = index(rest, t)) > 0) rest = substr(rest, 1, i-1) substr(rest, i+length(t)); if (index(tolower(rest), tolower(w)) == 0) next } print line }')"
      done < k8s.local/allowlist.txt
    fi
    if [[ -n "$hits" ]]; then echo "FOUND: private name '$word'"; echo "$hits" | sed 's/^/    /' | head -10; fail=1; fi
  done < k8s.local/denylist.txt
else
  echo "note: no k8s.local/denylist.txt, so cluster-specific names were not checked"
fi
for f in "${FILES[@]}"; do
  case "$f" in _cluster_backup/*|k8s.local/*|.env|*.db|*.db-wal|*.db-shm) echo "FOUND: private file would be published: $f"; fail=1 ;; esac
done
echo "scanned ${#FILES[@]} file(s): $([[ $fail -eq 0 ]] && echo clean || echo PROBLEMS FOUND)"
exit $fail
