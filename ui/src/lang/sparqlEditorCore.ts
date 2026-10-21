// Typed access to the shared SPARQL editor core (issue #68).
//
// The rules live in one plain-JavaScript file in the Python package,
// `arango_sparql/editor/sparql_editor_core.js`, which the
// contextual-data-fabric demo's editor also runs. It is a classic script
// (no exports, so it can be inlined into a page), so it is imported here for
// its side effect and read back from the global it registers. The types below
// mirror that file; `CORE_API_SHAPE` lists its keys and
// `sparqlEditorCore.test.ts` checks them against the runtime object, so a
// rename on either side fails a test instead of drifting.
import "../../../arango_sparql/editor/sparql_editor_core.js";

/** One class in editor vocabulary v1 (`arango_sparql.editor.EditorClassV1`). */
export interface EditorClass {
  props: string[];
  source: string;
  kind: string;
}

/** Editor vocabulary v1 (`arango_sparql.editor.EditorVocabV1`). */
export interface EditorVocab {
  base: string;
  classes: Record<string, EditorClass>;
}

export type SuggestionType =
  | "class"
  | "property"
  | "variable"
  | "keyword"
  | "function"
  | "namespace";

export interface Suggestion {
  label: string;
  insert: string;
  hint: string;
  /** Owning source kind for concepts, else "kw" / "var". */
  tag: string;
  type: SuggestionType;
}

/** `insert` replaces doc[from, to). Items are filtered and ordered already. */
export interface SuggestResult {
  from: number;
  to: number;
  items: Suggestion[];
}

export interface CoreOptions {
  /** The editor's default concept prefixes (the workbench uses ""). */
  conceptPrefixes?: string[];
}

export type ConceptRole = "class" | "property" | "unknown";

export interface ConceptClass {
  role: ConceptRole;
  kind: string | null;
}

export type TokenType =
  | "comment"
  | "concept"
  | "iri"
  | "string"
  | "open-string"
  | "variable"
  | "keyword"
  | "a";

export interface Token {
  from: number;
  to: number;
  type: TokenType;
  name?: string;
  role?: ConceptRole;
  kind?: string | null;
}

export interface HoverInfo {
  curie: string;
  iri: string;
  from: number;
  to: number;
}

export interface SparqlEditorCoreApi {
  VOCAB_CONTRACT: string;
  SPARQL_KEYWORDS: readonly string[];
  SPARQL_FUNCTIONS: readonly string[];
  WELL_KNOWN_PREFIXES: Readonly<Record<string, string>>;
  parsePrefixes(doc: string): Record<string, string>;
  usedPrefixes(doc: string): string[];
  missingPrefixes(doc: string): string[];
  prefixLine(prefix: string, iri: string): string;
  resolvableMissingDeclarations(doc: string): {
    resolvable: Array<{ prefix: string; iri: string }>;
    unknown: string[];
  };
  expandCurie(curie: string, prefixes: Record<string, string>): string | null;
  extractVars(doc: string): string[];
  hoverInfo(doc: string, pos: number): HoverInfo | null;
  conceptPrefixes(doc: string, vocab: EditorVocab | null, defaults?: string[]): string[];
  classifyConcept(name: string, vocab: EditorVocab | null): ConceptClass;
  tokenize(doc: string, vocab: EditorVocab | null, options?: CoreOptions): Token[];
  suggest(
    doc: string,
    caret: number,
    vocab: EditorVocab | null,
    options?: CoreOptions,
  ): SuggestResult | null;
  formatSparql(src: string): string;
}

/** Every key of the core's API; the compiler rejects a missing or extra one. */
export const CORE_API_SHAPE: Record<keyof SparqlEditorCoreApi, true> = {
  VOCAB_CONTRACT: true,
  SPARQL_KEYWORDS: true,
  SPARQL_FUNCTIONS: true,
  WELL_KNOWN_PREFIXES: true,
  parsePrefixes: true,
  usedPrefixes: true,
  missingPrefixes: true,
  prefixLine: true,
  resolvableMissingDeclarations: true,
  expandCurie: true,
  extractVars: true,
  hoverInfo: true,
  conceptPrefixes: true,
  classifyConcept: true,
  tokenize: true,
  suggest: true,
  formatSparql: true,
};

const registered = (globalThis as { SparqlEditorCore?: SparqlEditorCoreApi })
  .SparqlEditorCore;
if (!registered) {
  throw new Error(
    "sparql_editor_core.js loaded but did not register globalThis.SparqlEditorCore",
  );
}

export const core: SparqlEditorCoreApi = registered;
