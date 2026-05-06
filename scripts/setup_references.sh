#!/usr/bin/env bash
# Recreate the read-only sibling-repo symlinks under references/.
#
# Defaults assume sibling repos live in the same parent directory as this
# repo (e.g. ~/code/arango-sparql-py + ~/code/arango-cypher-py). Override
# with the *_PATH env vars if your layout differs.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PARENT="$(cd "$REPO_ROOT/.." && pwd)"

CYPHER_PATH="${ARANGO_CYPHER_PY_PATH:-$PARENT/arango-cypher-py}"
SPARQL_PATH="${ARANGO_SPARQL_PATH:-$PARENT/arango-sparql}"
MAPPER_PATH="${ARANGO_SCHEMA_MAPPER_PATH:-$PARENT/arango-schema-mapper}"

mkdir -p "$REPO_ROOT/references"
cd "$REPO_ROOT/references"

link() {
  local target="$1" name="$2"
  if [[ ! -d "$target" ]]; then
    echo "warning: $target does not exist; skipping $name link" >&2
    return
  fi
  ln -sfn "$target" "$name"
  echo "linked references/$name -> $target"
}

link "$CYPHER_PATH" arango-cypher-py
link "$SPARQL_PATH" arango-sparql
link "$MAPPER_PATH" arango-schema-mapper
