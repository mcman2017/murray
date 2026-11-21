#!/usr/bin/env bash
# Build and push the Murray image.
#   scripts/build.sh <registry>/<repo>:<tag>
# Environment: PLATFORM (default linux/arm64), BUILDER (default "default"), OS_PATCH_DATE (default: today, so the
# OS-package layer is rebuilt once a day and picks up security updates).
# The tag must be new: a tag that already exists in the registry is refused, because nodes that cached it would
# keep running the old image under the same name.
set -euo pipefail
IMAGE="${1:?usage: scripts/build.sh <registry>/<repo>:<tag>}"
ROOT="$(dirname "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")")"
PLATFORM="${PLATFORM:-linux/arm64}"
BUILDER="${BUILDER:-default}"
[[ "$IMAGE" == *:* && "$IMAGE" != *:latest ]] || { echo "use a tag that names one build (not 'latest')" >&2; exit 1; }
if command -v skopeo >/dev/null 2>&1; then
  if skopeo inspect --raw "docker://$IMAGE" >/dev/null 2>&1; then echo "refusing: $IMAGE already exists" >&2; exit 1; fi
elif docker manifest inspect "$IMAGE" >/dev/null 2>&1; then
  echo "refusing: $IMAGE already exists" >&2; exit 1
fi
docker buildx build --builder "$BUILDER" --platform "$PLATFORM" \
  --build-arg "OS_PATCH_DATE=${OS_PATCH_DATE:-$(date -u +%F)}" \
  --build-arg "VERSION=${IMAGE##*:}" \
  --target runtime -t "$IMAGE" --push "$ROOT"
echo "pushed $IMAGE"
