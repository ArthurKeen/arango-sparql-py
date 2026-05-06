# arango-sparql-py

Python-native SPARQL 1.1 → AQL transpiler and microservice for ArangoDB.

This is the modernization of the legacy JavaScript Foxx service
[`arango-sparql`](../arango-sparql/) and the sister project to
[`arango-cypher-py`](../arango-cypher-py/). It deliberately mirrors the
architecture of `arango-cypher-py` so a developer fluent in one repo can
read the other immediately.

## Architecture at a glance

| Concern               | Implementation                                                       |
| --------------------- | -------------------------------------------------------------------- |
| SPARQL parsing        | `rdflib.plugins.sparql.parser.parseQuery` + Algebra translation      |
| AQL emission          | Parameterized AQL builder (port of legacy `aql-query-builder.js`)    |
| Schema mapping        | OWL/Turtle ontology from `arango-schema-mapper`, loaded once into `rdflib.Graph` |
| HTTP service          | FastAPI (`arango_sparql.service`) — mirror of `arango_cypher.service`|
| NL → SPARQL           | `arango_sparql.nl2sparql` — mirror of `arango_cypher.nl2cypher`      |
| Reference triplestore | `pyoxigraph`, embedded, W3C-compliant — used as cross-validation gold|
| Test harnesses        | `pytest` + W3C SPARQL 1.1 DAWG runner (`tests/w3c/`)                 |
| Frontend              | Vite + React + TypeScript, CodeMirror SPARQL mode, Cytoscape.js      |

## Quickstart

```bash
# 1. Install (uv preferred)
uv sync --all-extras

# 2. Link in the sibling repos as read-only references
./scripts/setup_references.sh

# 3. Run the smoke tests
uv run pytest -q

# 4. Boot the service
uv run python main.py
# or
uv run arango-sparql-py serve --port 8000

# 5. (optional) Stand up ArangoDB
docker compose up -d
```

## Repository layout

```
arango_sparql/
  api.py                # public translate() entry point
  errors.py             # typed SparqlError hierarchy
  cli.py                # typer CLI (mirror of arango_cypher.cli)
  translate/
    parser.py           # rdflib parser wrapper
    visitor.py          # one visit_<NodeType> per Algebra op
    builder.py          # parameterized AQL query builder
    resolver.py         # OWL URI -> ArangoDB collection resolver
  service/
    app.py              # FastAPI app + CORS + public-mode guardrails
    models.py           # pydantic request/response models + _MAX_* limits
    routes/
      health.py
      sparql.py         # /translate, /execute
  nl2sparql/
    _core.py            # NL pipeline orchestrator + result dataclass
tests/
  translate/            # parser unit tests + golden tests
  cross/                # pyoxigraph cross-validation tests
  w3c/                  # W3C SPARQL 1.1 DAWG runner + harness
  nl2sparql/eval/       # NL eval harness (slow, gated)
  helpers/oxi.py        # pyoxigraph fixtures
ui/                     # Vite + React + TS frontend (placeholder)
references/             # symlinks to sibling repos (read-only)
docs/architecture/      # PRD, vision
.cursor/
  rules/                # scoped Cursor rules (000-context, 100-backend, ...)
  skills/sparql-to-aql/ # SPARQL→AQL porting recipe (read this first)
AGENTS.md               # shared agent contract for AI tooling
```

## For AI agents

Read [`AGENTS.md`](AGENTS.md) before making any change. It points at the
scoped rule files under `.cursor/rules/` and the porting recipe under
`.cursor/skills/sparql-to-aql/SKILL.md`.

## License

MIT.
