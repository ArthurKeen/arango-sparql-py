import { describe, it, expect, afterEach } from "vitest";
import {
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
