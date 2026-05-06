#!/usr/bin/env bash
# Fetch the W3C SPARQL 1.1 Evaluation Test Suite into tests/w3c/data/.
#
# The corpus is large and licence-restricted, so we don't vendor it. Run
# this once after cloning; CI runs the same script gated behind a
# RUN_W3C=1 flag.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$REPO_ROOT/tests/w3c/data"

mkdir -p "$DEST"
cd "$DEST"

if [[ -d sparql11-test-suite ]]; then
  echo "tests/w3c/data/sparql11-test-suite already present; skipping fetch"
  exit 0
fi

# Official W3C tarball mirror; replace with your preferred source if you
# want to pin to a known good revision.
URL="https://www.w3.org/2009/sparql/docs/tests/sparql11-test-suite-20121023.tar.gz"
echo "downloading $URL ..."
curl -fL "$URL" -o sparql11-test-suite.tar.gz
tar -xzf sparql11-test-suite.tar.gz
rm sparql11-test-suite.tar.gz
echo "extracted into $DEST"
