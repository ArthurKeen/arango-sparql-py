#!/usr/bin/env bash
# Build the BYOC tarball for the ArangoDB Container Manager (manual packaging).
#
# Companion to the arango-byoc-deploy tool, which uploads + deploys what this builds.
# See docs/BYOC_DEPLOYMENT.md.
#
# Layout: FLAT archive — `entrypoint` and `pyproject.toml` at the ROOT of the
# tar. `entrypoint` must be Python whose line-1 first token is literally
# `entrypoint` (the platform runs `python /project/$(awk '{print $1}' entrypoint)`).
# A nested layout fails inside the platform with "No entrypoint found".
#
# UI: bundled by default (the SPARQL workbench). `ui/vite.config.ts` uses
# base:"./" (relative asset URLs), so NO build-time mount prefix is needed —
# the bundle works under any Container-Manager path. Set PACKAGE_NO_UI=1 to
# ship a bare API (deploy with `arango-byoc-deploy --no-ui release`).
#
# .env: NOT bundled unless PACKAGE_INCLUDE_ENV=1 — a local .env typically holds
# OPENAI_API_KEY / ARANGO_PASSWORD etc. that must not leak into a shared tarball.
# Prefer Container Manager UI env vars for secrets.
#
# macOS: strip Apple xattrs / AppleDouble so GNU tar on the Linux cluster
# extracts cleanly.
set -euo pipefail
export COPYFILE_DISABLE=1

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:-${REPO_ROOT}/arango-sparql-py.tar.gz}"
STAGE="$(mktemp -d)"
cleanup() { rm -rf "${STAGE}"; }
trap cleanup EXIT

NAME="${PACKAGE_DIR_NAME:-arango-sparql-py}"
ROOTDIR="${STAGE}/${NAME}"
mkdir -p "${ROOTDIR}"

# --- source the editable install needs (pyproject + package + metadata files) --
cp -R "${REPO_ROOT}/arango_sparql" "${ROOTDIR}/"
cp "${REPO_ROOT}/pyproject.toml" "${ROOTDIR}/"
cp "${REPO_ROOT}/README.md" "${ROOTDIR}/"      # pyproject readme= (build metadata)
cp "${REPO_ROOT}/LICENSE" "${ROOTDIR}/"        # pyproject license=
[[ -f "${REPO_ROOT}/uv.lock" ]] && cp "${REPO_ROOT}/uv.lock" "${ROOTDIR}/"
cp "${REPO_ROOT}/entrypoint" "${ROOTDIR}/entrypoint"
chmod +x "${ROOTDIR}/entrypoint"

# Drop compiled caches so the archive is lean + reproducible.
find "${ROOTDIR}/arango_sparql" -name "__pycache__" -type d -prune -exec rm -rf {} + 2>/dev/null || true

# --- UI bundle (default on) ----------------------------------------------------
if [[ "${PACKAGE_NO_UI:-0}" != "1" ]]; then
    if [[ ! -d "${REPO_ROOT}/ui" ]]; then
        echo "error: no ui/ directory; set PACKAGE_NO_UI=1 to ship a bare API" >&2
        exit 1
    fi
    if ! command -v npm >/dev/null 2>&1; then
        echo "error: npm not on PATH (needed to build the UI); set PACKAGE_NO_UI=1 to skip" >&2
        exit 1
    fi
    echo "==> Building SPARQL workbench UI (relative base — no prefix needed)..."
    (
        cd "${REPO_ROOT}/ui"
        if [[ -f package-lock.json ]]; then npm ci; else npm install; fi
        rm -rf dist
        npm run build
    )
    if [[ ! -f "${REPO_ROOT}/ui/dist/index.html" ]]; then
        echo "error: ui build produced no ui/dist/index.html" >&2
        exit 1
    fi
    mkdir -p "${ROOTDIR}/ui"
    cp -R "${REPO_ROOT}/ui/dist" "${ROOTDIR}/ui/dist"
    echo "==> Bundled ui/dist (served by the app at the mount root)"
else
    echo "==> PACKAGE_NO_UI=1 — shipping bare API (deploy with --no-ui)"
fi

# --- optional baked .env (secrets — opt-in only) -------------------------------
if [[ "${PACKAGE_INCLUDE_ENV:-0}" == "1" ]]; then
    if [[ -f "${REPO_ROOT}/.env" ]]; then
        cp "${REPO_ROOT}/.env" "${ROOTDIR}/.env"
        echo "==> Bundled .env (PACKAGE_INCLUDE_ENV=1). Verify it holds no secrets you can't ship."
    else
        echo "==> PACKAGE_INCLUDE_ENV=1 but no .env at repo root — nothing bundled." >&2
    fi
elif [[ -f "${REPO_ROOT}/.env" ]]; then
    echo "==> Skipping .env (set PACKAGE_INCLUDE_ENV=1 to bake it; prefer Container Manager UI env for secrets)." >&2
fi

# --- bake ROOT_PATH (REQUIRED for a path-prefixed Container Manager mount) -----
# The platform does NOT forward app env at deploy time, so the service's mount
# prefix must be baked into a bundled .env (the FastAPI app reads it via
# load_dotenv() → root_path). Without it, /openapi.json, /docs and the API
# resolve at the CLUSTER root instead of under /_service/uds/_db/<db>/<instance>/
# (the UI still works — its asset URLs are relative). Mirrors arango-cypher-py's
# scripts/package-byoc.sh. Append, so it coexists with a baked full .env above.
if [[ -n "${SERVICE_ROOT_PATH:-}" ]]; then
    printf 'ROOT_PATH=%s\n' "${SERVICE_ROOT_PATH%/}" >> "${ROOTDIR}/.env"
    echo "==> Baked ROOT_PATH=${SERVICE_ROOT_PATH%/} into the bundle"
else
    echo "==> WARNING: no SERVICE_ROOT_PATH set — /openapi.json, /docs and the API" >&2
    echo "    will resolve at the cluster root, not under the service mount. Pass" >&2
    echo "    SERVICE_ROOT_PATH=/_service/uds/_db/<db>/<instance> to fix it." >&2
fi

# Strip lingering macOS xattrs (provenance/quarantine) that break Linux tar.
if [[ "$(uname -s)" == "Darwin" ]] && command -v xattr >/dev/null 2>&1; then
    xattr -cr "${ROOTDIR}" 2>/dev/null || true
fi

# Flat archive: entrypoint + pyproject.toml at the archive root.
tar -czf "${OUT}" -C "${ROOTDIR}" .
echo "Wrote ${OUT} (flat: entrypoint + pyproject.toml at archive root)"
