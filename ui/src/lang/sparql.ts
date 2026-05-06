// SPARQL syntax-highlighting wrapper.
//
// Mirrors `references/arango-cypher-py/ui/src/lang/cypher.ts`, but
// instead of hand-rolling a stream parser we wire CodeMirror's legacy
// SPARQL mode through `StreamLanguage`. This is the path mandated by
// `.cursor/rules/400-frontend-ui.mdc`:
//
// > Editor: CodeMirror 6 with `@codemirror/legacy-modes/mode/sparql`
// > for syntax highlighting.
//
// Completion + hover tooltips will land in a follow-up task; for now we
// just expose `sparql()` so `SparqlEditor.tsx` can drop it into its
// extensions list the same way `cypher()` was used in the Cypher UI.

import { LanguageSupport, StreamLanguage } from "@codemirror/language";
import { sparql as legacySparql } from "@codemirror/legacy-modes/mode/sparql";

export const sparqlLang = StreamLanguage.define(legacySparql);

export function sparql() {
  return new LanguageSupport(sparqlLang);
}
