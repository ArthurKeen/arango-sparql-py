# Releasing `arango-sparql-py` to PyPI

This repo is **not yet published** to PyPI (`pip install arango-sparql-py` → 404).
It runs fine from source and via the git-pinned dependency graph; publishing is
the separate **REQ-public-release-readiness** milestone. This runbook is the
scaffold for that milestone. The workflow that does the upload —
[`.github/workflows/publish.yml`](.github/workflows/publish.yml) — is **inert
until a `v*` tag is pushed**, and mirrors `arango-query-core`'s proven setup
(Trusted Publishing / OIDC, no API tokens).

## Prerequisite (blocker): repoint the `arango-query-core` dependency — ✅ DONE (#9)

**PyPI rejects any package whose `Requires-Dist` contains a direct URL** (a
`… @ git+https://…` reference). The `[nl]`/`[dense]` extras used to git-pin the
shared engine, which would have made this package unpublishable.

This is now resolved — PR #9 repointed both extras from `@ git+…@f2f3061` to the
published range (matching this repo's convention for its other published
dependency, `arangodb-schema-analyzer[...]>=0.9.0,<0.10.0`):

```toml
# [nl] extra
"arango-query-core>=0.2.0,<0.3.0"
# [dense] extra
"arango-query-core[dense]>=0.2.0,<0.3.0"
```

`arango-query-core 0.2.0` is on PyPI and verified to contain
`nl/postconditions.py` (the merged `f2f3061` work) plus the `dense`/`nl` extras.
The repoint also required implementing seam 8 (`path_index`/`path_prompt_section`)
on both adapters as an opt-out, since `0.2.0`'s engine calls `path_index()`
unconditionally — see #9. `uv.lock` was regenerated and the goldens re-run green
(CC-9). **No remaining direct-URL dependencies**, so `twine`/upload will not be
rejected.

> If a git pin ever creeps back in, `publish.yml`'s `twine check` plus the
> upload step will fail fast — repoint before tagging.

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

1. Dependency repoint — ✅ already landed (#9); no direct-URL deps remain.
2. Register the Trusted Publisher (above) if not already done.
3. Set the release version in `pyproject.toml` (`version = "X.Y.Z"`); update any
   changelog.
4. Tag and push — the tag is the trigger:
   ```bash
   git tag vX.Y.Z && git push origin vX.Y.Z
   ```
5. `publish.yml` runs **only** on `ArthurKeen/arango-sparql-py` (guarded against
   the `arango-solutions` mirror): it installs, runs the deterministic test
   gate, builds sdist+wheel, `twine check`s, and uploads via Trusted Publishing.
6. Verify: `pip install arango-sparql-py==X.Y.Z` in a clean venv.

## Notes

- `arango-sparql-py` now depends on the **published** `arango-query-core`
  (`>=0.2.0,<0.3.0`), so a future engine release within that band is picked up by
  a normal `uv lock` / reinstall — no more git-pin bumps for minor engine work.
- `arango-cypher-py` consumes the engine editable (`>=0.1.0,<0.2.0`); a published
  `0.2.0` is outside that range, so if it ever moves off the editable sibling
  install it needs its own range widened (tracked in that repo).
