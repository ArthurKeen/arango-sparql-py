// SPARQL editor intelligence (WP-UI-EDITOR): completion + hover + prefix
// management, as CodeMirror glue over the shared editor core.
//
// The rules (what to suggest at the caret, prefix/variable/CURIE helpers,
// the formatter) live in `arango_sparql/editor/sparql_editor_core.js`, which
// the contextual-data-fabric demo's editor runs too (issue #68); see
// `sparqlEditorCore.ts`. This file only adapts them to CodeMirror and holds
// the schema context `App` sets from `/schema/introspect`'s editor
// vocabulary — the same per-class list the NL prompt is grounded on.

import type {
  Completion,
  CompletionContext,
  CompletionResult,
} from "@codemirror/autocomplete";
import { core, type EditorVocab, type HoverInfo } from "./sparqlEditorCore";

export type { EditorVocab } from "./sparqlEditorCore";

export const {
  SPARQL_KEYWORDS,
  SPARQL_FUNCTIONS,
  WELL_KNOWN_PREFIXES,
  parsePrefixes,
  usedPrefixes,
  missingPrefixes,
  prefixLine,
  resolvableMissingDeclarations,
  expandCurie,
  extractVars,
  formatSparql,
} = core;

/** The workbench spells concepts with the ontology's default `:` prefix —
 * the form the NL prompt teaches the model to emit. */
export const WORKBENCH_CONCEPT_PREFIXES = [""];

// ---------------------------------------------------------------------------
// Schema context (set from React, mirrors setAqlSchemaContext)
// ---------------------------------------------------------------------------

let _vocab: EditorVocab | null = null;

/** The editor vocabulary completion runs on; `null` → keywords, variables
 * and prefixes only. */
export function setSparqlSchemaContext(vocab: EditorVocab | null): void {
  _vocab = vocab;
}

/**
 * The vocabulary rebased onto the ontology the workbench sends with NL
 * requests: the NL route reads that ontology's default `:` namespace before
 * the bundle's (`service/routes/nl.py`), so completion spells concepts in
 * the namespace the model is told to use. Unchanged when that ontology
 * declares no `:` or the same one.
 */
export function vocabForOntology(
  vocab: EditorVocab | null,
  ontologyTtl: string | null | undefined,
): EditorVocab | null {
  if (!vocab) return null;
  const ns = ontologyTtl ? parsePrefixes(ontologyTtl)[""] : undefined;
  return ns && ns !== vocab.base ? { ...vocab, base: ns } : vocab;
}

// ---------------------------------------------------------------------------
// Completion source (thin CM glue over the core's `suggest`)
// ---------------------------------------------------------------------------

export function sparqlCompletionSource(
  context: CompletionContext,
): CompletionResult | null {
  const doc = context.state.doc.toString();
  const result = core.suggest(doc, context.pos, _vocab, {
    conceptPrefixes: WORKBENCH_CONCEPT_PREFIXES,
  });
  if (!result) return null;
  const options: Completion[] = result.items.map((item) => ({
    label: item.insert,
    displayLabel: item.label,
    detail: item.hint,
    type: item.type,
  }));
  // `filter: false`: the core has already filtered and ranked the items (the
  // subject's own properties first), and CodeMirror then keeps that order
  // instead of fuzzy re-ranking. With no `validFor`, CM asks again on every
  // keystroke, so the core sees each new prefix.
  return { from: result.from, to: result.to, options, filter: false };
}

// ---------------------------------------------------------------------------
// Hover: expand the CURIE under the cursor to its full IRI
// ---------------------------------------------------------------------------

/**
 * The CURIE at `pos` and its expanded IRI (document prefixes override the
 * well-known ones), or null off a resolvable CURIE. Pure so the DOM
 * `hoverTooltip` wrapper in `SparqlEditor` stays trivial.
 */
export function sparqlHoverInfo(doc: string, pos: number): HoverInfo | null {
  return core.hoverInfo(doc, pos);
}
