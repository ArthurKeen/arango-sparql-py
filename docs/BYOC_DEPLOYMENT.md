# BYOC deployment — ArangoDB Container Manager

Deploy `arango-sparql-py` (the `/sparql` + NL API **and** the SPARQL workbench
UI, one process) into the **ArangoDB Platform "Container Manager"** that runs
alongside a customer's ArangoDB cluster — "bring your own cloud". This is the
standalone deployment story; the contextual-data-fabric embeds the transpiler as
a library instead.

Modelled on `arango-ontoextract`'s BYOC toolchain (same platform protocol). Two
pieces:

- **`scripts/package_arango_manual.sh`** — builds a flat `.tar.gz` of the Python
  source + the built UI + `entrypoint`.
- **`scripts/byoc_deploy.py`** — uploads it to the platform and (re)deploys the
  service, then verifies the right version is serving.

> **How it differs from a Docker deploy:** BYOC ships a **source tarball**, not
> an image. The platform runs it on a base image (`py12base`) that installs deps
> and launches uvicorn via the root `entrypoint` file. (The repo's `Dockerfile`
> is the *separate* image/registry path.)

## Prerequisites

1. **`.env` at the repo root** with the platform coordinator + credentials. The
   deploy script reads these to log into the Container Manager (and they double
   as the service's ArangoDB connection):

   | Var | Meaning |
   |---|---|
   | `ARANGO_URL` | platform coordinator URL, e.g. `https://prod.demo.pilot.arango.ai:8529` |
   | `ARANGO_USER` | platform user |
   | `ARANGO_PASSWORD` | platform password |
   | `ARANGO_DB` | database to scope the mount to (empty ⇒ global) |

   `.env` is gitignored — never commit it. (`ARANGO_ENDPOINT` / `ARANGO_USERNAME`
   are also accepted as cross-estate fallbacks.)
2. **`npm`** on PATH (the UI is built into the tarball) — or set `PACKAGE_NO_UI=1`.
3. `pip install requests` (only dependency the deploy script needs).

## Build the tarball

```bash
# UI bundled by default (Vite base:"./" → works under any mount path, no rebuild):
bash scripts/package_arango_manual.sh            # -> ./arango-sparql-py.tar.gz

# Bare API, no UI:
PACKAGE_NO_UI=1 bash scripts/package_arango_manual.sh

# Bake .env into the tarball (so the service has creds without pasting them into
# the Container Manager UI). OFF by default — the .env holds secrets:
PACKAGE_INCLUDE_ENV=1 bash scripts/package_arango_manual.sh
```

If you do **not** bake `.env`, supply `ARANGO_URL` / `ARANGO_USER` /
`ARANGO_PASSWORD` (and `ARANGO_DB`) as **Container Manager env vars** on the
service instead — that's the more secure path for shared clusters.

## Deploy

```bash
python3 scripts/byoc_deploy.py list                  # what's uploaded / deployed
python3 scripts/byoc_deploy.py update                # the usual: preflight → upload → swap → verify
python3 scripts/byoc_deploy.py verify --expect-version 0.2.0
python3 scripts/byoc_deploy.py rollback --to 0.2.0-1 # redeploy an uploaded build (code only)
python3 scripts/byoc_deploy.py delete                # remove the instance
```

`update` derives the release from `arango_sparql/__init__.py:__version__`,
uploads under `<version>-<n>`, then swaps the live service.

> ⚠️ **There is no in-place update.** A POST against a live instance fails with a
> Helm ownership error, so `update` **deletes then recreates** — the URL is down
> for ~60s (cold start on `py12base`). The upload happens *before* the delete, so
> a bad artifact fails while the old service still serves.

Defaults (override with flags): `--instance arango-sparql-py`, `--base-image
py12base`, UI on (`--no-ui` to disable), scope = `ARANGO_DB` (`--db ''` for
global). A wrong `--base-image` fails fast and lists the cluster's valid ones.

## The mount path + `ROOT_PATH` (important for the UI+API to align)

The platform serves the instance under a path prefix:

```
/_service/uds/_db/<db>/<instance>/        (or /_global/<instance>/ when --db '')
```

- The **UI** works there automatically (relative asset URLs).
- For the **API** (`/openapi.json`, `/sparql`, …) to answer under the same
  prefix, set the service env **`ROOT_PATH`** to that mount path (the deploy
  prints it). Add it to the baked `.env` or the Container Manager env vars, e.g.
  `ROOT_PATH=/_service/uds/_db/mydb/arango-sparql-py`.

## Verify

`update` finishes by polling `<mount>/` until HTTP 200, then asserting
`openapi.json`'s version equals the release, fetching every UI asset (200), and
hitting `<mount>/health`. Run it standalone any time:

```bash
python3 scripts/byoc_deploy.py verify --expect-version 0.2.0
```

## Health endpoints

- `GET <mount>/health` — liveness (`{"status":"ok","version":…}`)
- `GET <mount>/health/ready` — readiness; pings the configured ArangoDB (503 when unreachable)

## Environment variables the service reads

Beyond the `ARANGO_*` connection vars above: `ARANGO_SPARQL_PUBLIC_MODE`,
`CORS_ALLOWED_ORIGINS`, `ARANGO_SPARQL_CORS_CREDENTIALS`, `SESSION_TTL_SECONDS`,
`MAX_SESSIONS`, `OPENAI_API_KEY`/`ANTHROPIC_API_KEY` (NL), `ROOT_PATH` (above),
and `ARANGO_SPARQL_UI_DIR` (set automatically by `entrypoint` to the bundled
`ui/dist`; the service serves the SPA only when this points at a real bundle).
