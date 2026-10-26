#!/usr/bin/env bash
# Fetch the LDBC SNB Interactive v1 Turtle data set and substitution
# parameters into the cache (default ~/.cache/arango-sparql/ldbc, override
# with LDBC_CACHE_DIR). Archives are SHA-256 pinned in tests/ldbc/datasets.py
# and never vendored. Needs zstd.
#
#   scripts/fetch_ldbc.sh            # SF0.1
#   LDBC_SF=1 scripts/fetch_ldbc.sh  # another pinned scale factor
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
python -m tests.ldbc.datasets --sf "${LDBC_SF:-0.1}" --params
