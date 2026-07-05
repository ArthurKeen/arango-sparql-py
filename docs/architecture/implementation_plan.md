# Implementation plan — `arango-sparql-py`

This is the **living work-tracking** document. It complements the PRD:

- [`PRD.md`](PRD.md) is the **spec** (what "done" means, normative
  contracts, acceptance criteria) and the milestone **roadmap** (§14).
- This file is the **plan** (discrete work packages, their status, the
  files they touch, and their test signal). When the two disagree about
  intent, the PRD wins; when they disagree about *status*, this file is
  the source of truth.

Work packages use a stable `WP-<AREA>-<N|NAME>` id so they can be
referenced from commits and the PRD. Statuses: **Done**, **In progress**,
**Planned**, **Deferred**.

Numbering mirrors the sister project `arango-cypher-py`'s
`docs/implementation_plan.md` so cross-repo navigation is trivial. The
UI shell work packages (WP-UI-SHELL Phase 0–4) are the SPARQL-side port
of that project's chat-first "Query Workbench Shell"
(`references/arango-cypher-py/docs/query_workbench_shell_spec.md`).

---

## Tracking table

| WP | Area | Summary | PRD ref | Status |
| --- | --- | --- | --- | --- |
| WP-BE-OPT | Backend | Cross-subject `OPTIONAL` (RPT-native, ADR-0002 P1-A) + OPTIONAL-rebind-in-MINUS (P2) | App. B.2 | ✅ Done |
| WP-BE-DBBOOT | Backend | Dedicated-database auto-provision on boot | §A.4 | ✅ Done |
| WP-BE-MERGE | Backend | Merge analyzer-discovered physical mapping into inline ontology | §6.3.2 | ✅ Done |
| WP-BE-GRAPHSCOPE | Backend | Named-graph down-select: `/graphs`, `/session/graph`, scoped cache key | §6.8 | ✅ Done |
| WP-BE-LLMKEY | Backend | LLM provider resolution w/ standard-key fallback | §7.6 | ✅ Done |
| WP-BE-400 | Backend | `AQLQueryExecuteError` → `400` (not `500`) | §5.1 | ✅ Done |
| WP-UI-1 | UI | NL "Ask" bar + provenance/telemetry | §10.12 | ✅ Done |
| WP-UI-2 | UI | Schema-derived / optional-LLM query suggestions (`/nl-samples`) | §7.5, §10.12 | ✅ Done |
| WP-UI-3 | UI | Graph selector (named-graph scope pill) | §10.13 | ✅ Done |
| WP-UI-4 | UI | Ontology/mapping panel (Turtle + graph) + OWL roundtrip | §10.4 | ✅ Done |
| WP-UI-5 | UI | Schema introspect on connect + warning banner + refresh | §10.6 | ✅ Done |
| WP-UI-6 | UI | Results table/JSON/graph + literal-collapse toggle | §10.5 | ✅ Done |
| WP-UI-7 | UI | Query history + sample queries | §10.7 | ✅ Done |
| WP-UI-PROXY | UI | Dev vite proxy for `/mapping` + `/sample-queries` | §A.9 | ⬜ Planned |
| WP-UI-SHELL | UI | Chat-first workbench shell (Phase 0–4) | §10.0 | ⬜ Planned |
| WP-UI-CAT | UI | Schema-catalog readiness UX (pending/analyzing) | §10.17 | ⬜ Planned |
| WP-UI-GRAPH | UI | Schema-graph scalability (bundling/search/weight/expand) | §10.18 | ⬜ Planned |
| WP-UI-EDITOR | UI | SPARQL completion + hover + PrefixManager + explain/profile keymap | §10.2 | ⬜ Planned |
| WP-UI-AQL | UI | AQL edit-and-rerun wiring + `mapping` prop for completion | §10.3 | ⬜ Planned |
| WP-UI-EXPLAIN | UI | Explain/Profile result tabs + client wiring | §10.5 | ⬜ Planned |
| WP-UI-PALETTE | UI | Command palette (`Mod-K`) | §10.7 | ⬜ Planned |
| WP-UI-TENANT | UI | Mount `TenantSelector` when multitenancy detected | §10.6 | ⬜ Planned |
| WP-UI-CORR | UI | Cross-pane SPARQL↔AQL correspondence wired via translator source map | §10.2 | ⬜ Planned |
| WP-UI-A11Y | UI | a11y + i18n + Playwright + Lighthouse budgets | §10.10, §10.11 | ⬜ Planned (v1.1) |
| WP-UI-THEME | UI | Light theme | §10.8 | ⬜ Planned (v1.1) |

---

## UI shell migration — WP-UI-SHELL (the chat-first redesign)

Adopts the sister project's progressive-disclosure shell (§10.0) as the
canonical layout. Ships in phases so the workbench keeps working at every
checkpoint (per `incremental-over-atomic`). Each phase lands with vitest
coverage for the new pure logic and a green `cd ui && npm run build`.

### Phase 0 — `SettingsMenu` (gear consolidation)

- **New:** `ui/src/components/SettingsMenu.tsx` — gear popover.
- **Move into it:** Samples, History, Outline, Ontology toggle, and the
  auto-translate / auto-run / NL-mode / open-inspector-on-error toggles
  (today these are header buttons / app-local state in `App.tsx`).
- **Result:** header reduced to title + `ConnectionDialog` +
  `GraphSelector` (+ `TenantSelector` when mounted) + gear.
- **Verify:** reducer/toggle unit tests; header renders only the four
  allowed controls.

### Phase 1 — `ChatComposer` + `utils/pipeline.ts` (L0)

- **New:** `ui/src/components/ChatComposer.tsx` (§10.14) and
  `ui/src/utils/pipeline.ts` exporting `planSend(connected)` →
  `{ translate, run }`, plus `currentStage` / `stageLabel` / `isBusy`
  helpers (port + adapt SPARQL stage labels:
  "Generating SPARQL…" → "Transpiling to AQL…" → "Running…").
- **Wire:** Enter = Send full pipeline; Shift+Enter = newline; cancel
  while in flight; status strip (`role="status"`); disconnected =
  translate-only + "Connect to run".
- **Verify:** `pipeline.test.ts` for `planSend` / stage helpers;
  reducer provenance already covered by `store.test.ts`.

### Phase 2 — `QueryInspector` (L1 drawer)

- **New:** `ui/src/components/QueryInspector.tsx` (§10.15) — collapsible
  bottom drawer hosting `SparqlEditor` + `AqlEditor` in a drag-resizable
  split; power actions (Translate / Run / Explain / Profile / Format /
  AQL edit-and-rerun) relocated here from the header toolbar.
- **Persist (session-only):** `qi_height`, `qi_split`, `qi_sparql_open`,
  `qi_aql_open`.
- **Auto-open on error** (toggle from Phase 0): translate error → SPARQL
  pane; execute error → AQL pane w/ line highlight.
- **Verify:** inspector open/close + persistence unit tests.

### Phase 3 — per-result affordance chips + lazy-mount

- Chips under each result: `SPARQL · AQL · Explain · Profile · Graph ·
  Edit` that open the inspector focused on that surface.
- Lazy-mount heavy tabs (Cytoscape graph, large tables) only when
  selected (feeds §10.11 perf budgets).

### Phase 4 — multi-turn transcript (optional)

- Promote the single active query to a scrollable transcript of
  question → answer turns. Deferred unless demand emerges (matches the
  sister project's current partial state).

---

## Standalone UI work packages

### WP-UI-PROXY — dev proxy gap (quick win)

`ui/vite.config.ts` does not proxy `/mapping` or `/sample-queries`, so
dev-mode OWL import/export and API sample queries 404. Add both prefixes
to `proxiedPaths`. **Verify:** `curl :5173/mapping/export-owl` reaches
the backend.

### WP-UI-CAT — schema-catalog readiness UX (§10.17)

Add `schemaPending` / `schemaAnalyzing` to the store, poll
`introspectSchemaUntilReady` on connect / graph-scope change, and render
an amber "Schema is being analyzed" banner with **Check again** /
**Analyze now** rather than an indefinite spinner. Blocks nothing but
materially improves the demo. **Verify:** reducer tests for the pending
lifecycle.

### WP-UI-GRAPH — schema-graph scalability (§10.18)

Extend `SchemaGraph` / `CytoscapeSchemaGraph`: bundle object properties
by (domain, range) class pair (expand on click), weight arcs by analyzer
cardinality (§6.5), add a class/property search box, and expand members
incrementally. **Verify:** unit test the bundling transform.

### WP-UI-EDITOR — SPARQL editor parity (§10.2)

`ui/src/lang/sparql-completion.ts` + `sparql-hover.ts`; a
`PrefixManager.tsx`; extend the keymap with `Mod-Shift-E` (explain),
`Mod-Shift-P` (profile), `Mod-K` (palette — see WP-UI-PALETTE).

### WP-UI-AQL — AQL edit-and-rerun (§10.3)

Wire the existing `executeAql` client + `AqlEditor.onModified` into a Run
button that reads the live editor document; pass `mapping` to `AqlEditor`
so `var.property` completion works. **Verify:** reducer + a small DOM
test.

### WP-UI-EXPLAIN — explain/profile in results (§10.5)

Add Explain / Profile tabs to `ResultsPanel`, wiring the already-declared
`explainSparql` / `profileSparql` clients; render plan tree + hotspots.

### WP-UI-PALETTE — command palette (§10.7)

`Mod-K` palette listing all `SettingsMenu` actions + query actions.

### WP-UI-TENANT — tenant selector (§10.6)

Mount the existing `TenantSelector.tsx` when the backend reports a tenant
catalogue; bind via a session route mirroring `/session/graph`.

### WP-UI-CORR — cross-pane correspondence (§10.2)

Replace the heuristic `utils/correspondenceMap.ts` with translator
source-map metadata and wire hover-sync between the SPARQL and AQL panes
in `App.tsx`.

### WP-UI-A11Y — accessibility, i18n, and perf CI (§10.10, §10.11)

`ui/src/i18n/en.ts` + `t()` indirection; Playwright + axe-core suites;
`ui/lighthouse.json` + bundle-size gate; `prefers-reduced-motion`.
Targeted for v1.1 alongside WP-UI-THEME (light theme).

---

## Notes

- The object-centric `.cursor/rules/ui-architecture.mdc` (workspace
  canvas, context menus, never-collapse zones) describes a *different*
  product surface; the query workbench deliberately opts out of the
  never-collapse rule (§10.0). New UI work here follows
  `.cursor/rules/400-frontend-ui.mdc`.
- Keep parity with `references/arango-cypher-py/ui/` component-for-
  component where a SPARQL analogue exists; deviations (Turtle-centric
  ontology panel, RDF literal-collapse) are documented in PRD §10.
