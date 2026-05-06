# Contributing to arango-sparql-py

Thanks for your interest in contributing. This project is the Python
modernization of the legacy JavaScript Foxx [`arango-sparql`](https://github.com/ArthurKeen/arango-sparql)
service and intentionally mirrors the architecture of its sister project
[`arango-cypher-py`](https://github.com/ArthurKeen/arango-cypher-py) — read
either for reference patterns before proposing structural changes here.

## Development setup

```bash
git clone https://github.com/ArthurKeen/arango-sparql-py.git
cd arango-sparql-py

python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Optional: link sibling repos as read-only references for AI agents
# and porting work. Symlinks are gitignored; recreate locally as needed.
./scripts/setup_references.sh
```

## Running tests

```bash
# Translation unit + golden tests + cross-validation against pyoxigraph
# (no database needed; this is the CI-blocking baseline).
pytest -q

# With coverage
pytest -q --cov=arango_sparql

# W3C SPARQL 1.1 DAWG harness (translation-only; no live ArangoDB)
pytest -q tests/w3c -m w3c

# Refresh the W3C coverage report after touching the translator
python tests/w3c/analyze_coverage.py --write

# Live W3C harness (Docker required; runs translated AQL against
# ArangoDB and compares cursor bindings to the W3C-expected results)
docker compose up -d
RUN_INTEGRATION=1 pytest -q tests/w3c/test_w3c_live_execution.py

# NL→SPARQL evaluation harness (slow, gated)
RUN_EVAL=1 pytest -q tests/nl2sparql/eval
```

## Code style

This project uses [Ruff](https://docs.astral.sh/ruff/) for both linting and
formatting. CI enforces both gates.

```bash
ruff check .
ruff format --check .
```

Configuration lives in `pyproject.toml` under `[tool.ruff]`.

## Translator changes — the porting recipe

Every visitor method follows the deterministic 7-step recipe documented in
[`.cursor/skills/sparql-to-aql/SKILL.md`](.cursor/skills/sparql-to-aql/SKILL.md).
Read it before adding or modifying a `visit_<NodeType>` method. The recipe
covers:

1. Inspect the rdflib Algebra shape with `pprintAlgebra`
2. Implement the visitor against `arango_sparql.translate.builder.AqlQueryBuilder`
3. Add a YAML golden under `tests/translate/<construct>.yml`
4. Add a cross-validation case under `tests/cross/test_bgp_select_cross.py`
   (compares AQL output to pyoxigraph as the W3C reference store)
5. Re-run the W3C harness; XPASS counts should rise (or at minimum not regress)
6. Refresh `tests/w3c/COVERAGE_REPORT.md` via `analyze_coverage.py --write`
7. Update `references/arango-sparql/` mapping notes if porting legacy semantics

## Adding test cases

Translation goldens live in `tests/translate/<construct>.yml`. Each case has:

- `name` — unique identifier
- `sparql` — the input query
- `expected_aql` — the expected AQL output (verbatim, preserving whitespace)
- `expected_bind_vars` — the expected bind-variable dict

The corresponding Python test (`test_translate_<construct>_goldens.py`)
parametrizes over the YAML and asserts AQL + bind_vars exactly.

Cross-validation cases (`tests/cross/test_bgp_select_cross.py`) round-trip
the same query through both arango-sparql-py (via an in-memory AQL-subset
interpreter) and pyoxigraph, and assert bag- or order-equality on bindings.

## Project structure

- `arango_sparql/translate/` — parser, visitor, builder, schema resolver
- `arango_sparql/service/` — FastAPI app, models, routes (`/translate`, `/execute`, `/explain`, `/profile`, `/nl-*`)
- `arango_sparql/nl2sparql/` — NL → SPARQL pipeline (LLM-backed)
- `tests/translate/` — parser unit tests + YAML-driven goldens
- `tests/cross/` — pyoxigraph cross-validation
- `tests/w3c/` — W3C SPARQL 1.1 DAWG harness (translation-only + live)
- `tests/nl2sparql/` — NL pipeline unit tests + eval harness
- `ui/` — Vite + React + TypeScript frontend
- `references/` — symlinks to sibling repos (gitignored, recreated locally)
- `docs/architecture/` — PRD and vision documents
- `.cursor/rules/` — scoped Cursor rules for AI agents
- `.cursor/skills/sparql-to-aql/` — porting recipe (read first)

## For AI agents

Read [`AGENTS.md`](AGENTS.md) before making any change. It points at the
scoped rule files under `.cursor/rules/` and the porting recipe under
`.cursor/skills/sparql-to-aql/SKILL.md`.

## Reporting bugs

Please open an issue at <https://github.com/ArthurKeen/arango-sparql-py/issues>
with:

- a minimal SPARQL query that reproduces the problem,
- the OWL/Turtle ontology you loaded (or "default mapping" if you used the
  built-in resolver),
- the expected AQL vs. what you got (or the exception traceback).

For W3C-test discrepancies, please include the test ID (e.g. `bgp01`) so we
can pin the regression in the coverage report.
