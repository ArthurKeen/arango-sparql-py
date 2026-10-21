import { describe, it, expect, afterEach } from "vitest";
import { EditorState } from "@codemirror/state";
import { CompletionContext, type CompletionResult } from "@codemirror/autocomplete";
import {
  formatSparql,
  sparqlCompletionSource,
  vocabForOntology,
  defaultNamespace,
  type EditorVocab,
  parsePrefixes,
  usedPrefixes,
  missingPrefixes,
  resolvableMissingDeclarations,
  expandCurie,
  extractVars,
  prefixLine,
  sparqlHoverInfo,
  setSparqlSchemaContext,
  WELL_KNOWN_PREFIXES,
} from "./sparqlComplete";

const Q = `PREFIX ex: <http://example.org/>
PREFIX foaf: <http://xmlns.com/foaf/0.1/>
SELECT ?person ?name WHERE {
  ?person a ex:Person ;
          foaf:name ?name .
  OPTIONAL { ?person foaf:mbox ?email }
}`;

afterEach(() => setSparqlSchemaContext(null));

describe("parsePrefixes", () => {
  it("parses SPARQL PREFIX declarations", () => {
    const p = parsePrefixes(Q);
    expect(p.ex).toBe("http://example.org/");
    expect(p.foaf).toBe("http://xmlns.com/foaf/0.1/");
  });

  it("parses turtle @prefix declarations (incl. default prefix)", () => {
    const p = parsePrefixes("@prefix : <http://d/> .\n@prefix rdf: <http://r/> .");
    expect(p[""]).toBe("http://d/");
    expect(p.rdf).toBe("http://r/");
  });

  it("ignores IRIs and returns empty for none", () => {
    expect(parsePrefixes("SELECT * WHERE { ?s ?p ?o }")).toEqual({});
  });
});

describe("usedPrefixes / missingPrefixes", () => {
  it("finds prefixes referenced in the body", () => {
    const used = usedPrefixes(Q).sort();
    expect(used).toContain("ex");
    expect(used).toContain("foaf");
  });

  it("does not count declared prefixes as usages", () => {
    // ex/foaf appear both declared and used; a prefix only in a PREFIX
    // line must not be reported as used.
    const doc = "PREFIX only: <http://x/>\nSELECT * WHERE { ?s ?p ?o }";
    expect(usedPrefixes(doc)).not.toContain("only");
  });

  it("does not trip on http inside IRIs or strings", () => {
    const doc = 'SELECT * WHERE { ?s ?p "not:aprefix" . ?s ?p <http://x/y> }';
    expect(usedPrefixes(doc)).toEqual([]);
  });

  it("reports used-but-undeclared prefixes", () => {
    const doc = "SELECT ?s WHERE { ?s a foaf:Person . ?s skos:note ?n }";
    expect(missingPrefixes(doc).sort()).toEqual(["foaf", "skos"]);
  });

  it("returns no missing when everything is declared", () => {
    expect(missingPrefixes(Q)).toEqual([]);
  });
});

describe("resolvableMissingDeclarations", () => {
  it("splits well-known from unknown prefixes", () => {
    const doc = "SELECT ?s WHERE { ?s a foaf:Person . ?s custom:x ?y }";
    const { resolvable, unknown } = resolvableMissingDeclarations(doc);
    expect(resolvable).toEqual([
      { prefix: "foaf", iri: WELL_KNOWN_PREFIXES.foaf },
    ]);
    expect(unknown).toEqual(["custom"]);
  });
});

describe("expandCurie", () => {
  const prefixes = { ex: "http://example.org/" };
  it("expands a known prefix", () => {
    expect(expandCurie("ex:Person", prefixes)).toBe("http://example.org/Person");
  });
  it("returns null for unknown prefix", () => {
    expect(expandCurie("nope:Person", prefixes)).toBeNull();
  });
  it("returns null when there is no colon", () => {
    expect(expandCurie("Person", prefixes)).toBeNull();
  });
});

describe("extractVars", () => {
  it("collects distinct ? and $ variable names without sigils", () => {
    expect(extractVars(Q).sort()).toEqual(["email", "name", "person"]);
  });
  it("ignores variable-like text inside strings", () => {
    expect(extractVars('SELECT ?a WHERE { ?a ?p "?fake" }').sort()).toEqual([
      "a",
      "p",
    ]);
  });
});

describe("prefixLine", () => {
  it("formats a PREFIX declaration", () => {
    expect(prefixLine("ex", "http://example.org/")).toBe(
      "PREFIX ex: <http://example.org/>",
    );
  });
});

describe("sparqlHoverInfo", () => {
  it("expands the CURIE under the cursor", () => {
    const idx = Q.indexOf("ex:Person") + 2; // inside the token
    const info = sparqlHoverInfo(Q, idx);
    expect(info?.curie).toBe("ex:Person");
    expect(info?.iri).toBe("http://example.org/Person");
  });

  it("returns null off a CURIE", () => {
    const idx = Q.indexOf("SELECT") + 1;
    expect(sparqlHoverInfo(Q, idx)).toBeNull();
  });

  it("ignores URL schemes", () => {
    const doc = "SELECT * WHERE { ?s ?p <http://x/y> }";
    const idx = doc.indexOf("http") + 1;
    expect(sparqlHoverInfo(doc, idx)).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Completion: CodeMirror glue over the shared core (issue #68)
// ---------------------------------------------------------------------------

const VOCAB: EditorVocab = {
  base: "http://example.org/onto#",
  classes: {
    Person: { props: ["name", "knows"], source: "arangodb:db", kind: "arangodb" },
    Company: { props: ["name", "founded"], source: "arangodb:db", kind: "arangodb" },
  },
};

function complete(doc: string, explicit = false): CompletionResult | null {
  const state = EditorState.create({ doc });
  return sparqlCompletionSource(new CompletionContext(state, doc.length, explicit));
}

function labels(doc: string): string[] {
  return (complete(doc)?.options ?? []).map((o) => o.label);
}

describe("sparqlCompletionSource", () => {
  it("offers only classes after `a`, spelled with the default prefix", () => {
    setSparqlSchemaContext(VOCAB);
    expect(labels("SELECT * WHERE { ?p a :")).toEqual([":Person", ":Company"]);
  });

  it("scopes predicates to the subject's declared class, then lists classes", () => {
    setSparqlSchemaContext(VOCAB);
    expect(labels("SELECT * WHERE { ?c a :Company ; :")).toEqual([
      ":name",
      ":founded",
      ":Person",
      ":Company",
    ]);
  });

  it("keeps the core's order and replaces the typed token", () => {
    setSparqlSchemaContext(VOCAB);
    const doc = "SELECT * WHERE { ?c a :Company ; :fo";
    const result = complete(doc);
    expect(result?.filter).toBe(false);
    expect(result?.from).toBe(doc.length - ":fo".length);
    expect(result?.to).toBe(doc.length);
    expect(result?.options[0]).toEqual({
      label: ":founded",
      displayLabel: ":founded",
      detail: "Company",
      type: "property",
    });
  });

  it("completes the full-IRI spelling, matching on the IRI and showing it short", () => {
    setSparqlSchemaContext(VOCAB);
    const result = complete("SELECT * WHERE { ?c a <http://example.org/onto#Comp");
    expect(result?.options).toEqual([
      {
        label: "<http://example.org/onto#Company>",
        displayLabel: "…#Company",
        detail: "class · arangodb:db",
        type: "class",
      },
    ]);
  });

  it("offers no concepts without a vocabulary, and nothing for a foreign prefix", () => {
    setSparqlSchemaContext(null);
    expect(complete("SELECT * WHERE { ?p a :")).toBeNull();
    setSparqlSchemaContext(VOCAB);
    expect(complete("SELECT * WHERE { ?p xsd:")).toBeNull();
  });

  it("still completes keywords, variables and prefix names", () => {
    setSparqlSchemaContext(null);
    expect(labels("SEL")).toEqual(["SELECT"]);
    expect(labels("SELECT ?person WHERE { ?per")).toEqual(["?person"]);
    expect(labels("SELECT * WHERE { ?s fo")).toContain("foaf:");
  });
});

describe("formatSparql (shared core)", () => {
  it("pretty-prints, moving only whitespace", () => {
    const q = "SELECT ?s WHERE { ?s a :Person ; :name ?n . }";
    expect(formatSparql(q)).toBe("SELECT ?s WHERE {\n  ?s a :Person ;\n    :name ?n .\n}");
  });

  it("leaves a query with # comments unchanged", () => {
    const q = "SELECT ?s WHERE { # people\n ?s a :Person }";
    expect(formatSparql(q)).toBe(q);
  });
});

describe("vocabForOntology", () => {
  it("rebases onto the default namespace of the ontology NL requests carry", () => {
    const ttl = "@prefix : <http://mine.example/#> .\n:Person a owl:Class .";
    expect(vocabForOntology(VOCAB, ttl)?.base).toBe("http://mine.example/#");
    expect(vocabForOntology(VOCAB, ttl)?.classes).toBe(VOCAB.classes);
  });

  it("keeps the vocabulary when the ontology names no default namespace, or the same one", () => {
    expect(vocabForOntology(VOCAB, "@prefix ex: <http://e/> .")).toBe(VOCAB);
    expect(vocabForOntology(VOCAB, `@prefix : <${VOCAB.base}> .`)).toBe(VOCAB);
    expect(vocabForOntology(VOCAB, "")).toBe(VOCAB);
    expect(vocabForOntology(null, "@prefix : <http://mine.example/#> .")).toBeNull();
  });
});

describe("defaultNamespace", () => {
  // Mirrors the server's default_namespace (arango_sparql/schema/vocabulary.py)
  // so the editor and the NL prompt pick the same `:`.
  it("takes the first Turtle @prefix, else the first SPARQL PREFIX", () => {
    expect(defaultNamespace("@prefix : <http://a/> .\n@prefix : <http://b/> .")).toBe("http://a/");
    expect(defaultNamespace("PREFIX : <http://s/>\n@prefix : <http://t/> .")).toBe("http://t/");
    expect(defaultNamespace("PREFIX : <http://s/>")).toBe("http://s/");
    expect(defaultNamespace("@prefix ex: <http://e/> .")).toBeNull();
  });
});
