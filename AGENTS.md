# AGENTS.md — shared agent contract for `arango-sparql-py`

This file is the single, model-agnostic contract for any AI coding agent
operating in this repository (Cursor, Claude Code, Codex CLI, GitHub
Copilot Workspace, etc.). It complements — but does not replace — the
project rules under `.cursor/rules/`.

If you are an agent reading this for the first time, **read this file
fully before making any edit**, then consult the rule files referenced
below as needed.

## Mission

Build `arango-sparql-py`, a Python microservice that transpiles
SPARQL 1.1 queries into ArangoDB AQL, with an NL→SPARQL pipeline,
a Vite/React UI, and a W3C-compliant test harness. It modernizes the
legacy JavaScript Foxx service `arango-sparql` and is the sister
project to `arango-cypher-py`.

## Hard rules (memorize)

1. **Use `rdflib` for SPARQL parsing.** Never ANTLR, never a custom
   parser. Entry point is
   `rdflib.plugins.sparql.parser.parseQuery` →
   `rdflib.plugins.sparql.algebra.translateQuery`.
2. **Use the AQL query builder for all AQL emission.** Bind variables
   only — never inline literals or hand-concatenate AQL strings.
3. **Port translation semantics from `references/arango-sparql/src/lib/`,
   not from your training data.** When in doubt, read the JS first.
4. **Mirror `references/arango-cypher-py/`'s structure.** FastAPI app
   factory, route modules, pydantic models, multitenancy guards,
   `pyproject.toml` shape, `tests/` layout — all should be a
   one-to-one analog so cross-repo navigation is trivial.
5. **`pyoxigraph` is the W3C ground truth for tests.** Cross-validation
   tests run the same SPARQL against `pyoxigraph` and against the
   transpiled AQL, then compare bindings.

## Where to look

| Concern                          | Source of truth                                                      |
| -------------------------------- | -------------------------------------------------------------------- |
| Always-on identity & guardrails  | `.cursor/rules/000-project-context.mdc`                              |
| Backend Python conventions       | `.cursor/rules/100-backend-python.mdc`                               |
| Testing rules + W3C harness      | `.cursor/rules/200-testing.mdc`                                      |
| NL→SPARQL pipeline               | `.cursor/rules/300-nl2sparql.mdc`                                    |
| Frontend UI                      | `.cursor/rules/400-frontend-ui.mdc`                                  |
| Spec, vision, ADRs, roadmap      | `docs/architecture/PRD.md` (single source of truth; vision = App. C, ADRs = App. B) |
| Work tracking (WP status)        | `docs/architecture/implementation_plan.md` (living plan; PRD = spec, this = status) |
| SPARQL→AQL porting recipe        | `.cursor/skills/sparql-to-aql/SKILL.md`                              |
| Architecture template            | `references/arango-cypher-py/`                                       |
| Translation semantics (legacy)   | `references/arango-sparql/src/lib/`                                  |
| OWL/Turtle schema generator      | `references/arango-schema-mapper/`                                   |

## Workflow expectations

1. **Plan before edit.** For any non-trivial change, produce a short
   plan (or update the project todo list) before touching files.
2. **Read before write.** Always read a file with the appropriate tool
   before editing it; never make blind edits.
3. **Mimic, don't invent.** When implementing a new module, first look
   for the analogous module in `references/arango-cypher-py/` and copy
   its structure. Deviations should be justified in the PR description.
4. **One test per behavior.** Every translation feature lands with a
   golden test (`tests/translate/*_goldens.py`) and, where W3C semantics
   apply, a `pyoxigraph` cross-validation test.
5. **Surface unsupported SPARQL early.** Raise `UnsupportedSparqlError`
   with a clear message and a stable error code; never emit silently
   wrong AQL.
6. **Search shared memory before planning.** Before `gsd-plan-phase`
   (or during `gsd-discuss-phase`), run `/pattern-search` on the phase
   goal to surface team-verified solutions from the sibling repos
   (`arango-cypher-py`, legacy `arango-sparql`). Treat hits as *inputs*
   to planning — never a replacement for GSD's own researcher/planner.
7. **Save verified patterns after review.** After `gsd-code-review`
   passes and `gsd-extract-learnings` runs, promote only the
   cross-repo-reusable, verified subset via `/pattern-save` (translation
   recipes, `pyoxigraph` cross-validation gotchas, `UnsupportedSparqlError`
   conventions). Never save unverified guesses or project-only trivia.
   Requires the shared-memory MCP to be live.

## Off-limits

- Modifying anything under `references/` (those are symlinks to other
  repos — read-only).
- Adding new top-level dependencies without updating `pyproject.toml`
  and running `uv lock`.
- Committing secrets (`.env` files), generated reports
  (`tests/**/eval/reports/`), or vendored W3C test data.
- Changing CI behavior to skip failing tests instead of fixing them.

## Communication

- When you cannot proceed because of an architectural ambiguity, stop
  and ask. Better to surface the uncertainty than to invent a pattern
  that contradicts `references/arango-cypher-py/`.
- When you make a non-obvious tradeoff (e.g. choosing iterative vs.
  recursive Algebra walking), note it in code comments and link to the
  relevant rule file or sibling repo source.
