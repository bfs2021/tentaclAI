#!/usr/bin/env bash
# Rebuild the brand images in docs/brand/ (social preview, wordmarks) from docs/brand/src/.
# Edit texts/styles in docs/brand/src/gen.py. Needs Docker (Playwright image) and internet for the Inter font.
# The topology screenshot comes from docs/screenshots/topology.png. Upload social-preview.png in GitHub → Settings.
set -euo pipefail
cd "$(dirname "$0")/.."
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
cp control/app/static/logo.svg docs/screenshots/topology.png docs/brand/src/render.mjs "$work/"
python3 docs/brand/src/gen.py "$work"
docker run --rm -v "$work:/b" -w /b mcr.microsoft.com/playwright:v1.55.0-noble \
    bash -c 'npm i -s playwright@1.55.0 >/dev/null 2>&1 && node render.mjs'
cp "$work"/social-preview.png "$work"/wordmark-on-dark.png "$work"/wordmark-on-light.png docs/brand/
echo "→ docs/brand/social-preview.png, wordmark-on-dark.png, wordmark-on-light.png"
