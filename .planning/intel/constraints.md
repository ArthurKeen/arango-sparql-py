# Constraints (SPEC intel)

Extracted from classified SPECs. One SPEC in the ingest set:
`.cursor/rules/300-nl2sparql.mdc` (a Cursor rule file whose content is pure
implementation contract — data model, module structure, forbidden behaviours).
Precedence: SPEC sits below ADR and above PRD/DOC.

All entries share:
- source: /Users/plosiewicz/dev/arango-sparql-py/.cursor/rules/300-nl2sparql.mdc
- applies-to: `arango_sparql/nl2sparql/**/*.py`, `tests/nl2sparql/**/*.py`

---

## CON-nl2sparql-module-layout (protocol)
The NL→SPARQL pipeline mirrors `arango_cypher/nl2cypher/`:
- `_core.py` — pipeline orchestrator + `NL2SparqlResult` dataclass.
- `providers.py` — `LLMProvider` protocol + `OpenAIProvider`, `AnthropicProvider`, test-only `ScriptedProvider` mock.
- `fewshot.py` — BM25-backed few-shot index over a curated corpus.
- `tenant_guardrail.py` + `tenant_scope.py` — physical-tenant collection rewriting and prompt-side scope hints, enforced before the AQL runs.
- `entity_resolution.py` — optional ER hook (mirrors Cypher's).
- `_aql.py` — final AQL emission via the same parser/visitor/builder pipeline `/translate` uses (never asks the LLM for AQL).

## CON-nl2sparql-result-dataclass (api-contract)
`NL2SparqlResult` carries: `sparql`, `explanation`, `confidence`, `method`
(`"llm"` | `"rule_based"` | `"cached"`), `schema_context`, `prompt_tokens`,
`completion_tokens`, `total_tokens`, `cached_tokens`, `retries`. Must match
`NL2CypherResult` field-for-field where semantics carry over (cross-repo
telemetry).

## CON-nl2sparql-prompt-turtle (protocol)
Always provide the OWL ontology in Turtle (`.ttl`) as schema context (LLMs read
Turtle better than JSON-LD/XML), generated from `arango-schema-mapper`'s OWL
output. Prompt header must pin the dialect (`SPARQL 1.1`), forbid vendor
extensions, require fully-qualified IRIs (no invented bare prefixes), and
constrain output to a fenced ```sparql``` block.

## CON-nl2sparql-fewshot-budget (nfr)
Few-shot examples come from the BM25 index over a curated YAML corpus; never
inline more than 3 shots (token budget).

## CON-nl2sparql-corrections-store (schema)
Persist user corrections in `nl_corrections.db` (SQLite, WAL mode), identical
schema shape to Cypher. Correction lookups happen before any LLM call.

## CON-nl2sparql-forbidden (protocol)
- MUST NOT ask the LLM to emit AQL directly — the LLM's job is SPARQL only; the
  deterministic transpiler turns SPARQL into AQL.
- MUST NOT inline the entire OWL ontology when a tenant-scoped slice would do —
  pre-trim the Turtle to the tenant's reachable classes/properties.

---

> Note: The PRD also carries heavy normative technical constraints (§5.2 media-type
> negotiation & documented error contract, §6.2 `phys:*` OWL mapping vocabulary,
> §6.3.2 analyzer hard-dependency ≥ 0.6.1, §6.3.4 startup-guard env vars, §8.6
> STRIDE). Those are captured as requirements/context intel (PRD is precedence
> DOC-tier below SPEC) rather than duplicated here; see requirements.md and
> context.md.
