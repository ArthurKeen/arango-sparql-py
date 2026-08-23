# Releasing `arango-sparql-py` to PyPI

This repo is **not yet published** to PyPI (`pip install arango-sparql-py` → 404).
It runs fine from source and via the git-pinned dependency graph; publishing is
the separate **REQ-public-release-readiness** milestone. This runbook is the
scaffold for that milestone. The workflow that does the upload —
[`.github/workflows/publish.yml`](.github/workflows/publish.yml) — is **inert
until a `v*` tag is pushed**, and mirrors `arango-query-core`'s proven setup
(Trusted Publishing / OIDC, no API tokens).

## Prerequisite (blocker): repoint the `arango-query-core` dependency

**PyPI rejects any package whose `Requires-Dist` contains a direct URL** (a
`… @ git+https://…` reference). `pyproject.toml` currently git-pins the shared
engine in two extras:

```toml
# [nl] extra
"arango-query-core @ git+https://github.com/ArthurKeen/arango-query-core@f2f3061…"
# [dense] extra
"arango-query-core[dense] @ git+https://github.com/ArthurKeen/arango-query-core@f2f3061…"
```

Both must become **published-version** specifiers before the first tag. This is
now safe: `arango-query-core 0.2.0` is on PyPI and **verified to contain
`nl/postconditions.py`** (the merged `f2f3061` work) plus the `dense`/`nl`
extras. Matching this repo's own convention for its other published dependency
(`arangodb-schema-analyzer[...]>=0.9.0,<0.10.0`), use a compatible range:

```toml
# [nl] extra
"arango-query-core>=0.2.0,<0.3.0"
# [dense] extra
"arango-query-core[dense]>=0.2.0,<0.3.0"
```

Then (CC-9 — a deliberate dependency change; consumers re-run goldens):

```bash
uv lock                                   # regenerate uv.lock off the new spec
pip install -e ".[dev,nl,service]"        # pulls arango-query-core 0.2.0 from PyPI
pytest -m "not integration and not w3c and not eval" -q   # goldens must stay green
ruff check . && ruff format --check . && mypy
```

> The stale `uv.lock` only affects `uv`-based dev flows; `pip install`/`python -m
> build`/CI read `pyproject.toml` directly, so the publish pipeline itself is
> unaffected by lock timing. Still, regenerate it in the same change.

## One-time: register the PyPI Trusted Publisher

On PyPI → *Your projects* (or *Publishing* for a not-yet-existing project) →
**Add a pending publisher (GitHub)** — exact values for this repo:

| Field | Value |
|---|---|
| PyPI Project Name | `arango-sparql-py` |
| Owner | `ArthurKeen` |
| Repository name | `arango-sparql-py` |
| Workflow name | `publish.yml` |
| Environment name *(optional 5th field)* | `pypi` |

No API token is stored anywhere — the `publish` job authenticates via OIDC
(`id-token: write`) from the `pypi` environment.

## Cut a release

1. Land the dependency repoint above (its own PR, green CI).
2. Set the release version in `pyproject.toml` (`version = "X.Y.Z"`); update any
   changelog.
3. Tag and push — the tag is the trigger:
   ```bash
   git tag vX.Y.Z && git push origin vX.Y.Z
   ```
4. `publish.yml` runs **only** on `ArthurKeen/arango-sparql-py` (guarded against
   the `arango-solutions` mirror): it installs, runs the deterministic test
   gate, builds sdist+wheel, `twine check`s, and uploads via Trusted Publishing.
5. Verify: `pip install arango-sparql-py==X.Y.Z` in a clean venv.

## Notes

- Publishing the engine (`arango-query-core`) does **not** change this repo
  until the repoint above is merged — today it still resolves `@f2f3061` from
  git and is unaffected by the 0.2.0 release.
- `arango-cypher-py` consumes the engine editable (`>=0.1.0,<0.2.0`); a published
  `0.2.0` is outside that range, so if it ever moves off the editable sibling
  install it needs its own range widened (tracked in that repo).
