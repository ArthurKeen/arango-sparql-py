// SPARQL editor intelligence (WP-UI-EDITOR): completion + hover + prefix
// management. All logic here is pure and CM-agnostic (types from
// @codemirror/autocomplete are type-only and erased at build), so it runs
// under the node test env and the `SparqlEditor` component only has to
// wrap `sparqlCompletionSource` / `sparqlHoverInfo` in CM extensions.
//
// Mirrors the schema-context pattern in `lang/aql.ts` (a module-level
// context set from React) but tuned for SPARQL: completion of keywords,
// declared + well-known prefixes, CURIE local names from the schema, and
// `?variables` already present in the document.

import type {
  Completion,
  CompletionContext,
  CompletionResult,
} from "@codemirror/autocomplete";

export const SPARQL_KEYWORDS = [
  "SELECT", "CONSTRUCT", "ASK", "DESCRIBE", "WHERE", "FROM", "PREFIX",
  "BASE", "OPTIONAL", "FILTER", "UNION", "MINUS", "GRAPH", "SERVICE",
  "BIND", "VALUES", "GROUP BY", "ORDER BY", "HAVING", "LIMIT", "OFFSET",
  "DISTINCT", "REDUCED", "AS", "ASC", "DESC", "NOT EXISTS", "EXISTS",
  "IN", "true", "false", "a",
];

export const SPARQL_FUNCTIONS = [
  "STR", "LANG", "LANGMATCHES", "DATATYPE", "BOUND", "IRI", "URI",
  "BNODE", "RAND", "ABS", "CEIL", "FLOOR", "ROUND", "CONCAT", "STRLEN",
  "UCASE", "LCASE", "ENCODE_FOR_URI", "CONTAINS", "STRSTARTS", "STRENDS",
  "STRBEFORE", "STRAFTER", "YEAR", "MONTH", "DAY", "HOURS", "MINUTES",
  "SECONDS", "TIMEZONE", "TZ", "NOW", "UUID", "STRUUID", "MD5", "SHA1",
  "SHA256", "COALESCE", "IF", "STRLANG", "STRDT", "sameTerm", "isIRI",
  "isURI", "isBLANK", "isLITERAL", "isNUMERIC", "REGEX", "SUBSTR",
  "REPLACE", "COUNT", "SUM", "MIN", "MAX", "AVG", "SAMPLE", "GROUP_CONCAT",
];

// Common namespaces so completion + hover work before the user has typed
// their own PREFIX lines. Declarations parsed from the document override
// these on collision.
export const WELL_KNOWN_PREFIXES: Record<string, string> = {
  rdf: "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
  rdfs: "http://www.w3.org/2000/01/rdf-schema#",
  owl: "http://www.w3.org/2002/07/owl#",
  xsd: "http://www.w3.org/2001/XMLSchema#",
  foaf: "http://xmlns.com/foaf/0.1/",
  skos: "http://www.w3.org/2004/02/skos/core#",
  dc: "http://purl.org/dc/elements/1.1/",
  dct: "http://purl.org/dc/terms/",
  sh: "http://www.w3.org/ns/shacl#",
  ex: "http://example.org/",
};

// Blank out IRIs, string literals, and comments so prefix detection
// doesn't trip over `http://…` inside `<>` or `foo:` inside a string.
function stripNoise(doc: string): string {
  return doc
    .replace(/<[^>]*>/g, " ")
    .replace(/"(?:[^"\\]|\\.)*"/g, '""')
    .replace(/'(?:[^'\\]|\\.)*'/g, "''")
    .replace(/#[^\n]*/g, " ");
}

const PREFIX_DECL_RE = /(?:PREFIX|@prefix)\s+([A-Za-z][\w.-]*)?\s*:\s*<([^>]*)>/gi;

/** Parse `PREFIX x: <iri>` and `@prefix x: <iri> .` into { x: iri }. */
export function parsePrefixes(doc: string): Record<string, string> {
  const out: Record<string, string> = {};
  let m: RegExpExecArray | null;
  PREFIX_DECL_RE.lastIndex = 0;
  while ((m = PREFIX_DECL_RE.exec(doc)) !== null) {
    out[m[1] ?? ""] = m[2];
  }
  return out;
}

/** Prefixes referenced as `x:local` in the body (excluding declarations). */
export function usedPrefixes(doc: string): string[] {
  const body = stripNoise(doc).replace(
    /(?:PREFIX|@prefix)\s+[\w.-]*\s*:\s*/gi,
    " ",
  );
  const set = new Set<string>();
  const re = /(?:^|[\s(;,.[<])([A-Za-z][\w.-]*):[A-Za-z0-9_%-]/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(body)) !== null) set.add(m[1]);
  return [...set];
}

/** Prefixes used but not declared in the document. */
export function missingPrefixes(doc: string): string[] {
  const declared = parsePrefixes(doc);
  return usedPrefixes(doc).filter((p) => !(p in declared));
}

/** Format a single SPARQL PREFIX declaration line. */
export function prefixLine(prefix: string, iri: string): string {
  return `PREFIX ${prefix}: <${iri}>`;
}

/**
 * PREFIX lines for missing prefixes that we *can* resolve from the
 * well-known table. Unknown prefixes (no canonical IRI) are returned
 * separately so the UI can prompt for their IRI instead of guessing.
 */
export function resolvableMissingDeclarations(doc: string): {
  resolvable: Array<{ prefix: string; iri: string }>;
  unknown: string[];
} {
  const resolvable: Array<{ prefix: string; iri: string }> = [];
  const unknown: string[] = [];
  for (const p of missingPrefixes(doc)) {
    if (WELL_KNOWN_PREFIXES[p]) resolvable.push({ prefix: p, iri: WELL_KNOWN_PREFIXES[p] });
    else unknown.push(p);
  }
  return { resolvable, unknown };
}

/** Expand a `prefix:local` CURIE to a full IRI, or null if unresolvable. */
export function expandCurie(
  curie: string,
  prefixes: Record<string, string>,
): string | null {
  const i = curie.indexOf(":");
  if (i < 0) return null;
  const pfx = curie.slice(0, i);
  const local = curie.slice(i + 1);
  const ns = prefixes[pfx];
  if (ns === undefined) return null;
  return ns + local;
}

/** Distinct `?var` / `$var` names present in the document (no sigil). */
export function extractVars(doc: string): string[] {
  const set = new Set<string>();
  const re = /[?$]([A-Za-z_]\w*)/g;
  let m: RegExpExecArray | null;
  const body = stripNoise(doc);
  while ((m = re.exec(body)) !== null) set.add(m[1]);
  return [...set];
}

// ---------------------------------------------------------------------------
// Schema context (set from React, mirrors setAqlSchemaContext)
// ---------------------------------------------------------------------------

export interface SparqlSchemaContext {
  /** Class local names (e.g. "Person") offered after `a` / in type position. */
  classes: string[];
  /** Predicate local names (e.g. "knows") offered in predicate position. */
  properties: string[];
}

let _sparqlCtx: SparqlSchemaContext | null = null;

export function setSparqlSchemaContext(ctx: SparqlSchemaContext | null): void {
  _sparqlCtx = ctx;
}

// ---------------------------------------------------------------------------
// Completion source (thin CM glue over the pure helpers above)
// ---------------------------------------------------------------------------

export function sparqlCompletionSource(
  context: CompletionContext,
): CompletionResult | null {
  const word = context.matchBefore(/[?$]?[\w:]*/);
  if (!word || (word.from === word.to && !context.explicit)) return null;
  const text = word.text;
  const doc = context.state.doc.toString();
  const options: Completion[] = [];

  if (text.startsWith("?") || text.startsWith("$")) {
    const sigil = text[0];
    for (const v of extractVars(doc)) {
      options.push({ label: sigil + v, type: "variable" });
    }
  } else if (text.includes(":")) {
    const pfx = text.slice(0, text.indexOf(":"));
    const ctx = _sparqlCtx;
    if (ctx) {
      for (const c of ctx.classes) {
        options.push({ label: `${pfx}:${c}`, type: "class", boost: 5 });
      }
      for (const p of ctx.properties) {
        options.push({ label: `${pfx}:${p}`, type: "property", boost: 3 });
      }
    }
  } else {
    for (const kw of SPARQL_KEYWORDS) {
      options.push({ label: kw, type: "keyword" });
    }
    for (const fn of SPARQL_FUNCTIONS) {
      options.push({ label: fn, type: "function" });
    }
    const prefixes = { ...WELL_KNOWN_PREFIXES, ...parsePrefixes(doc) };
    for (const [p, iri] of Object.entries(prefixes)) {
      if (p) options.push({ label: `${p}:`, type: "namespace", detail: iri });
    }
  }

  if (options.length === 0) return null;
  return { from: word.from, options, validFor: /^[?$]?[\w:]*$/ };
}

// ---------------------------------------------------------------------------
// Hover: expand the CURIE under the cursor to its full IRI
// ---------------------------------------------------------------------------

/**
 * Given the whole document and a character offset, return the CURIE under
 * the cursor and its expanded IRI (using document + well-known prefixes),
 * or null when the cursor isn't on a resolvable CURIE. Pure so the DOM
 * `hoverTooltip` wrapper in `SparqlEditor` stays trivial.
 */
export function sparqlHoverInfo(
  doc: string,
  pos: number,
): { curie: string; iri: string; from: number; to: number } | null {
  // Expand left/right over CURIE characters around the cursor.
  const isCurieChar = (ch: string) => /[A-Za-z0-9_.:%-]/.test(ch);
  let from = pos;
  let to = pos;
  while (from > 0 && isCurieChar(doc[from - 1])) from--;
  while (to < doc.length && isCurieChar(doc[to])) to++;
  const token = doc.slice(from, to);
  if (!token || !token.includes(":")) return null;
  // Ignore anything that looks like a URL scheme (http://…).
  if (/^[a-z]+:\/\//i.test(token)) return null;
  const prefixes = { ...WELL_KNOWN_PREFIXES, ...parsePrefixes(doc) };
  const iri = expandCurie(token, prefixes);
  if (iri === null) return null;
  return { curie: token, iri, from, to };
}
