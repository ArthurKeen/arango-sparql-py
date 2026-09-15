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
| Schema analyzer (CSI / OWL)      | `references/arango-schema-analyzer/`                                 |

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

## Optional: dark-factory drift mode

If the local `.claude/` skills are present, this project supports autonomous PRD
drift detection:

- `/pattern-search <problem>` before solving a non-trivial problem.
- `/pattern-save` after fixing a drift gap or finding a reusable technique.
- `/prd-sync` at the end of any session that touched implementation files.

Drift policy: a MISSING requirement is a bug (not a TODO); a TEST-ONLY requirement
(tested but not implemented) must be fixed; never mark a requirement IMPLEMENTED
without a `file:line` reference. Shared memory lives in ArangoDB via the
`arangodb-memory-mcp` server (`shared_patterns`, `project_registry`, `drift_alerts`).

Identity for the drift skills:

- PROJECT_ID: `arango-sparql-py`
- PROJECT_TYPE: `microservice`
- PRD_FILE: `docs/architecture/PRD.md`
- TECH_STACK: Python, FastAPI, rdflib, ArangoDB/AQL, pyoxigraph, Vite/React

## Communication

- When you cannot proceed because of an architectural ambiguity, stop
  and ask. Better to surface the uncertainty than to invent a pattern
  that contradicts `references/arango-cypher-py/`.
- When you make a non-obvious tradeoff (e.g. choosing iterative vs.
  recursive Algebra walking), note it in code comments and link to the
  relevant rule file or sibling repo source.


## Engineering rules

<!-- shared-engineering-rules -->
Shared across the Arango tooling estate. Agents that read `AGENTS.md` (Claude
Code, Codex) get these; Cursor reads `.cursor/rules/` instead, so where this repo
also has `.cursor/rules/*.mdc` covering the same ground, **both copies are
tracked — change a rule in both places.**

### Read before write
Match the codebase, don't fight it. Before writing anything, search for how it is
already done: existing patterns, utilities, similar types, endpoint shapes, test
helpers, error conventions. If a helper exists, call it — don't write a second
one. Reuse the established naming, file organisation, error handling and logging
style. Your job is to make the codebase *more* consistent, not less.

### Surface, don't guess
A wrong guess implemented is worse than an honest question asked. Try to resolve
uncertainty yourself first (codebase, docs, tests). If it is still unclear —
ambiguous requirements, several valid approaches, a breaking change, an
architectural choice, unclear scope — state what you understand, what is
unclear, and the options with trade-offs, then wait. Confidence is not a
substitute for the user's knowledge.

### Incremental over atomic
Small steps that individually work beat large steps that eventually work. Each
increment must be verifiable, reversible, reviewable in under five minutes, and
deployable on its own. At every checkpoint the tree must compile, pass tests and
be functional. Refactor by adding alongside, migrating callers one at a time,
then removing the old. Warning signs: more than ~5 files at once, an hour with no
commit, or a change you cannot describe in one sentence — slice smaller.

### Test what you touch
Changed code means changed behaviour, and behaviour needs a test. New function →
unit test. New endpoint → integration test. Bug fix → a regression test that
would have caught it. Refactor → existing tests must pass, and if they don't
cover the path, add coverage *first*. For every file you modify, check there is a
test file, that it covers what you changed, and that you actually ran it. Tests
must be deterministic, fast, isolated, clearly named, and assert behaviour rather
than absence of a crash. "I tested manually" and "I'll add tests later" are not
acceptable.

### Verify before claiming done
If you didn't run it, you didn't ship it. Before saying work is complete: build
it, test it, actually exercise the changed path, and look at the output. Frontend:
`npm run type-check`, `lint`, `test`, `build`. Backend: the test suite, `ruff`,
`mypy`. Claiming "I've implemented X" without verifying X works is not a mistake,
it's a false statement.

### Comprehensiveness over simplification
This is production software; simplification is the enemy of completeness. Every
change must address error handling (no swallowed errors — every handler does
something, with messages saying what failed, with what input, and why), edge
cases (empty/nil, boundaries, invalid input, unicode, concurrency), configuration
rather than hardcoding for anything that could differ across environments, test
completeness, observability (structured logging, metrics, health), security
(validate at boundaries, no secrets in code or logs, authz checks), documentation,
UI states (loading / error / empty / success, plus accessibility), data integrity
(validation, constraints, transactions, idempotency) and performance (pagination,
caching, indexing). Never ship happy-path-only, empty catch blocks, magic numbers,
copy-paste divergence, or `console.log` debugging.

### Wiring over deletion
Unused code usually means a missing feature, not garbage. A linter warning about
an unused import, variable or parameter is a request to *finish the
implementation*, not to delete it. Do not delete "unused" code without proving it
is genuinely obsolete. An unused `ctx` gets passed down; an unused `err` gets
handled; an unused prop gets wired to rendering or logic; a `useEffect` missing
dependencies gets a stabilised dependency via `useCallback`/`useMemo` — never an
`eslint-disable`. Anything added in the previous turn is mandatory to use. A
passing lint check on a broken feature is a failure.

### Modularity and structure
Everything has a place. Size limits: source 1500 lines, tests 2000, config 500,
docs 1000 — past that, split. Split also when you cannot find things without
searching or the file holds unrelated concerns. Placement: shared types in the
core/types module, tests beside their source (except E2E), configs in `configs/`,
scripts in `scripts/` — not the repo root. Each module should have one
responsibility, a minimal public API, hidden internals, and be independently
testable. A new developer should be able to find any file from what it does,
without searching.

### Mock fidelity
A test that passes against a wrong-signature mock is a test that didn't run.
Before mocking any production symbol, open the real declaration and read it; the
mock's constructor args, method signatures and field types must mirror the real
ones so a signature change breaks the test. Never infer a mock's shape from how
the test reads it, and never use `any` to make a mock "flexible" — that disables
the safety net. This rule exists because of a real bug: a mocked `ApiError` took
`(status, message)` while the real class takes `(status, body)`; every test was
internally consistent, CI was green, and the error-handling assertions proved
nothing. Jest specifics are in `frontend/AGENTS.md`.

### Checkpoint regularly
Commit early, push often; large uncommitted changesets are disasters waiting to
happen. Commit when a feature is complete, when tests pass, before a risky
refactor, before switching tasks, and before ending a session. One logical change
per commit — if you can't describe it in one line it's too big. Use scoped,
descriptive messages (`fix(curation): atomic reparent endpoint`), not "updates" or
"wip".

## Arango UI design rules

All interfaces must match the ArangoDB web platform (Agentic AI Suite, chat,
GraphRAG). Do not introduce shades outside these groups.

### Brand and hierarchy
Clean and professional with generous white space. Arango Green is the action
colour (primary buttons, links, checkmarks, active/selected states). Dark gray
text on white in content areas. The side menu is solid black with white icons and
labels. Red is for errors and deletions only, used sparingly.

### Typography
**Inter** globally — no decorative fonts. Headings semi-bold and clearly larger
than body text; body text regular. A simple monospace (e.g. Courier) for code,
technical blocks and inline variables.

### Colour

Greens — primary and actions:

| Name | Value | Usage |
| --- | --- | --- |
| Light green background | `#f4fef2` | Selected tabs, chips, pills |
| Arango green (main) | `#006532` | Primary buttons, checkmarks, links, active states |
| Dark green hover | `#005329` | Hover on primary green buttons |
| Brand green | `#007339` | Logo representations and charts |

Grays — text and layout:

| Name | Value | Usage |
| --- | --- | --- |
| Page background | `#ffffff` | Main content background |
| Light gray background | `#f8f8f8` | Panels, tables, code blocks |
| Borders | `#e5e5e5` | Subtle separators |
| Body text | `#282828` | Paragraphs, labels, standard text |
| Muted text | `#9a9a9a` | Helper text, hints, secondary info |

Interface specifics:

| Name | Value | Usage |
| --- | --- | --- |
| Error / delete | `#da1a20` | Error messages, destructive buttons |
| Left menu | `#000000` | Narrow sidebar background |
| Menu hover | white @ 15–20% opacity | Side-menu hover highlight |

### Brand assets
**Avocado icon** — the square mark only, used in the narrow left side menu or as
the app icon. **Full logo** — Avocado plus the "ArangoDB" wordmark in dark text,
only on white or pale pages.

### Screen patterns

**Home / AI Suite landing** — white or soft background image; title must read
exactly `"Arango Agentic AI Suite"`; clean cards with short feature descriptions
and prominent Arango Green `"Run"` buttons.

**Chat interfaces (GraphRAG, Ada, …)** — white chat canvas; text input bounded by
thin gray borders; Arango Green for send and success elements; responses rendered
as clean markdown with headings, bullets and light-gray code boxes.

**Forms and settings** — clean white background; readable dark-gray descriptive
labels above or beside inputs; a primary Arango Green confirm button alongside a
secondary gray cancel.

**Data visualisations and graphs** — light gray workspace canvas; Arango Green
accents for selected nodes and paths; nodes and text labels must stay legible
against the canvas.
