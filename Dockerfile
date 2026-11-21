# syntax=docker/dockerfile:1
# Murray: one image, two roles (MURRAY_ROLE=app | sandbox).
#   docker buildx build --platform linux/arm64 -t <registry>/murray:<tag> .
#   docker buildx build --target test -t murray:test . && docker run --rm --network none murray:test

# ---- css: compile Tailwind once, at build time (no CDN at runtime) -------------------------------------------
FROM python:3.12-slim-bookworm AS css
ARG TARGETARCH
ARG TAILWIND_VERSION=v4.3.5
ARG TAILWIND_SHA256_ARM64=ffcd0d21dbd7c605dc12743ff0c0b47debc963c86b399674084c1c8f2c96ecad
ARG TAILWIND_SHA256_AMD64=e47695cee3f0d90623a91f3939b08bdee610eb5ad99340adff9d4145b284d6cc
RUN <<'SH'
set -eu
case "${TARGETARCH}" in
  arm64) asset=tailwindcss-linux-arm64; sum="${TAILWIND_SHA256_ARM64}" ;;
  amd64) asset=tailwindcss-linux-x64;   sum="${TAILWIND_SHA256_AMD64}" ;;
  *) echo "unsupported arch ${TARGETARCH}" >&2; exit 1 ;;
esac
python - "$asset" "$sum" "${TAILWIND_VERSION}" <<'PY'
import hashlib, sys, urllib.request
asset, want, version = sys.argv[1], sys.argv[2], sys.argv[3]
url = f"https://github.com/tailwindlabs/tailwindcss/releases/download/{version}/{asset}"
data = urllib.request.urlopen(url, timeout=300).read()
got = hashlib.sha256(data).hexdigest()
if got != want:
    sys.exit(f"tailwind checksum mismatch: {got} != {want}")
open("/usr/local/bin/tailwindcss", "wb").write(data)
PY
chmod +x /usr/local/bin/tailwindcss
SH
WORKDIR /build
COPY web ./web
RUN tailwindcss -i web/input.css -o web/static/app.css --minify

# ---- deps: OS packages and Python dependencies ----------------------------------------------------------------
FROM python:3.12-slim-bookworm AS deps
ARG OS_PATCH_DATE=unset
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MPLBACKEND=Agg MPLCONFIGDIR=/tmp/mpl HOME=/tmp
# OS_PATCH_DATE busts the cache so a rebuild picks up current security updates.
RUN echo "os patch level: ${OS_PATCH_DATE}" \
 && apt-get update && apt-get -y upgrade \
 && apt-get install -y --no-install-recommends tini ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 14101 --no-create-home --shell /usr/sbin/nologin murray \
 && useradd --uid 14102 --no-create-home --shell /usr/sbin/nologin sandbox
WORKDIR /app
COPY requirements.txt ./
RUN pip install -r requirements.txt
# matplotlib's font cache, built once; the sandbox copies it into each job's config directory so a cold import
# does not spend seconds of the job's CPU budget scanning fonts.
RUN MPLCONFIGDIR=/opt/mpl python -c "import matplotlib.pyplot" && chmod -R a+rX /opt/mpl

# ---- runtime ---------------------------------------------------------------------------------------------------
FROM deps AS runtime
COPY murray ./murray
COPY eval ./eval
COPY web ./web
COPY --from=css /build/web/static/app.css ./web/static/app.css
ARG VERSION=dev
ENV MURRAY_VERSION=${VERSION}
USER 14101
EXPOSE 8000 8100
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "murray"]

# ---- test ------------------------------------------------------------------------------------------------------
FROM runtime AS test
USER root
COPY requirements-dev.txt ./
RUN pip install -r requirements-dev.txt
COPY tests ./tests
USER 14101
CMD ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"]
