# `references/` — read-only sibling-repo links

These are **symlinks** to the sibling projects on disk. They are *not*
checked in (see `.gitignore`) — every developer creates them locally with
`scripts/setup_references.sh` (or by re-running the symlink commands
below).

## Why

Cursor and other AI agents work dramatically better when the source code
they are asked to mimic or port is reachable from the workspace root. By
linking these repos under `references/`, the agent can:

- read `arango-cypher-py/` to mirror the FastAPI / pydantic / pytest
  scaffolding,
- read the legacy `arango-sparql/` JS to port translation semantics
  (BGPs, OPTIONAL, FILTER, regex, …),
- read `arango-schema-mapper/` to understand the OWL/Turtle schema
  contract this service consumes.

## Links

| Path                              | Purpose                                                 |
| --------------------------------- | ------------------------------------------------------- |
| `references/arango-cypher-py/`    | Architecture template (Python, FastAPI, nl2cypher, UI). |
| `references/arango-sparql/`       | Legacy Foxx service — SPARQL→AQL translation semantics. |
| `references/arango-schema-mapper/`| OWL/Turtle ontology generator.                          |

## Recreate the links

```bash
mkdir -p references
ln -sfn ../../arango-cypher-py        references/arango-cypher-py
ln -sfn ../../arango-sparql           references/arango-sparql
ln -sfn ../../arango-schema-mapper    references/arango-schema-mapper
```

(Adjust the relative paths if your sibling repos do not live next to this
one under `~/code/`.)

## Hands off

Treat everything under `references/` as **read-only**. If you need to
change a sibling project, do it in that repo and PR there — never edit
through the symlink.
