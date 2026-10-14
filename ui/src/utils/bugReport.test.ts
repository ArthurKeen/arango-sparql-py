import { describe, expect, it } from "vitest";

import {
  DEFAULT_TITLE,
  ISSUE_REPOSITORY,
  MAX_ISSUE_URL_LENGTH,
  MAX_TITLE_LENGTH,
  PUBLIC_NOTE,
  buildDetails,
  buildIssue,
  composeBody,
  maskIris,
  maskValues,
  sliceChars,
  type ReportContext,
} from "./bugReport";

// Ported from arango-cypher-py's ui/src/utils/bugReport.test.ts; the errors
// below are what this service really reports (captured from
// arango_sparql.api.translate).
const base: ReportContext = {
  appVersion: "0.2.0",
  analyzerVersion: "0.14.3",
  error: "class IRI 'http://acme-bank.example/ont#PrivateBanking' is not declared owl:Class in the ontology",
  userAgent: "Mozilla/5.0 Test",
  includeQuery: false,
  sparql: 'SELECT ?c WHERE { ?c <http://acme-bank.example/ont#ssn> "123-45-6789" }',
  aql: "FOR c IN @@collection FILTER c.ssn == @v RETURN c",
};

describe("buildDetails", () => {
  it("leads with the versions", () => {
    const d = buildDetails(base);
    expect(d.startsWith("## Environment")).toBe(true);
    expect(d).toContain("- arango-sparql-py: 0.2.0");
    expect(d).toContain("- arangodb-schema-analyzer: 0.14.3");
  });

  it("marks versions it could not read as unknown", () => {
    expect(buildDetails({ ...base, appVersion: null, analyzerVersion: null })).toContain(
      "- arango-sparql-py: unknown\n- arangodb-schema-analyzer: unknown",
    );
  });

  it("masks values the error echoes from the query unless the query is included", () => {
    expect(buildDetails(base)).not.toContain("acme-bank");
    expect(buildDetails(base)).toContain("class IRI '…' is not declared owl:Class");
    expect(buildDetails({ ...base, includeQuery: true })).toContain("acme-bank.example/ont#PrivateBanking");
  });

  it("reduces a bare IRI in the error to its local name unless the query is included", () => {
    const error = "unsupported predicate <http://acme-bank.example/ont#accountNumber> in OPTIONAL";
    expect(buildDetails({ ...base, error })).toContain("unsupported predicate <…#accountNumber> in OPTIONAL");
    expect(buildDetails({ ...base, error, includeQuery: true })).toContain("acme-bank.example");
  });

  it("never includes the SPARQL or AQL by default", () => {
    const d = buildDetails(base);
    expect(d).not.toContain("```sparql");
    expect(d).not.toContain("123-45-6789");
    expect(d).not.toContain("@@collection");
  });

  it("includes the SPARQL and AQL when the user ticks it", () => {
    const d = buildDetails({ ...base, includeQuery: true });
    expect(d).toContain('```sparql\nSELECT ?c WHERE { ?c <http://acme-bank.example/ont#ssn> "123-45-6789" }\n```');
    expect(d).toContain("```aql\nFOR c IN @@collection");
  });

  it("keeps a code block intact when the text itself contains backticks", () => {
    expect(buildDetails({ ...base, error: "bad token ``` here", includeQuery: true })).toContain(
      "````text\nbad token ``` here\n````",
    );
  });

  it("treats a missing or blank error as none", () => {
    for (const error of [null, "", "  \n "]) {
      expect(buildDetails({ ...base, error })).toContain("(no error message shown)");
    }
  });
});

describe("maskValues", () => {
  it("masks what a SPARQL parse error quotes from the query", () => {
    expect(maskValues("failed to parse SPARQL: Expected SelectQuery, found 'FILTER'  (at char 106), (line:1, col:107)")).toBe(
      "failed to parse SPARQL: Expected SelectQuery, found '…'  (at char 106), (line:1, col:107)",
    );
  });

  it("masks from the first opening quote to the last closing quote, nested echoes included", () => {
    expect(maskValues("near 'FILTER(?n = 'prod-admin')' x")).toBe("near '…' x");
  });

  it("does not treat apostrophes in prose as quotes", () => {
    expect(maskValues("Can't resolve property ssn on Person")).toBe("Can't resolve property ssn on Person");
    expect(maskValues("the reply's JSON did not parse (no object)")).toBe(
      "the reply's JSON did not parse (no object)",
    );
  });

  it("masks backtick and typographic quotes", () => {
    expect(maskValues("unknown label `AcmeCustomers` here")).toBe("unknown label '…' here");
    expect(maskValues("value ‘secret’ and “x” and «y» end")).toBe("value '…' end");
  });

  it("masks long digit runs such as account numbers and ids", () => {
    expect(maskValues("document users/8812345 not found; card 4417123456789113")).toBe(
      "document users/… not found; card …",
    );
    expect(maskValues("line 12, column 34")).toBe("line 12, column 34");
  });

  it("masks a dangling quote to the end of its line only", () => {
    expect(maskValues("collection or view not found\nnear 'abc")).toBe("collection or view not found\nnear '…'");
  });
});

describe("maskIris", () => {
  it("keeps the local name and drops the namespace", () => {
    expect(maskIris("<http://acme-bank.example/ont#salary>")).toBe("<…#salary>");
    expect(maskIris("<https://data.acme.example/people/Person>")).toBe("<…/Person>");
    expect(maskIris("<urn:acme:secret>")).toBe("<…>");
  });

  it("masks every IRI on a line", () => {
    expect(maskIris("<http://a.example/x#p> joins <http://b.example/y/q>")).toBe("<…#p> joins <…/q>");
  });

  it("leaves comparisons and prose alone", () => {
    expect(maskIris("FILTER(?a < ?b && ?b > 3)")).toBe("FILTER(?a < ?b && ?b > 3)");
    expect(maskIris("expected <something else>")).toBe("expected <something else>");
  });
});

describe("composeBody", () => {
  it("puts the public notice and the user's text first", () => {
    const body = composeBody("It crashed when I clicked Run", "## Environment");
    expect(body.startsWith(PUBLIC_NOTE)).toBe(true);
    expect(body).toContain("## What happened\n\nIt crashed when I clicked Run\n\n## Environment");
  });
});

describe("sliceChars", () => {
  it("never splits a character in two", () => {
    const cut = sliceChars("a".repeat(79) + "😀 rest", 80);
    expect(cut.endsWith("😀")).toBe(true);
    expect(() => encodeURIComponent(cut)).not.toThrow();
  });
});

describe("buildIssue", () => {
  const details = buildDetails(base);

  it("files on this service's own repository", () => {
    expect(ISSUE_REPOSITORY).toBe("arango-solutions-org/arango-sparql-py");
    expect(buildIssue("t", "", details).url.startsWith(`https://github.com/${ISSUE_REPOSITORY}/issues/new?`)).toBe(
      true,
    );
  });

  it("opens the repository's pre-filled new-issue page", () => {
    const { url, shortened } = buildIssue("T & x", "B", "## Environment", "acme/repo");
    expect(url.startsWith("https://github.com/acme/repo/issues/new?title=T%20%26%20x&body=")).toBe(true);
    expect(shortened).toBe(false);
  });

  it("uses the default title when the title is blank", () => {
    expect(decodeURIComponent(buildIssue("  ", "", details).url)).toContain(`title=${DEFAULT_TITLE}`);
  });

  it("does not throw on emoji at any cut point", () => {
    for (let n = 1500; n < 1700; n += 7) {
      const d = buildDetails({ ...base, includeQuery: true, sparql: 'SELECT * { ?s ?p "' + "😀".repeat(n) + '" }' });
      expect(() => buildIssue("t", "😀👍🏽 ".repeat(n), d)).not.toThrow();
    }
  });

  it("shortens the user's text first, so a long description keeps the versions and the error", () => {
    const { url, shortened } = buildIssue("t", "x".repeat(20000), details);
    expect(url.length).toBeLessThanOrEqual(MAX_ISSUE_URL_LENGTH);
    expect(shortened).toBe(true);
    const text = decodeURIComponent(url);
    expect(text).toContain("arangodb-schema-analyzer: 0.14.3");
    expect(text).toContain("## Error");
    expect(text).toContain("Report shortened to fit");
  });

  it("keeps the versions when a long query forces trimming", () => {
    const d = buildDetails({ ...base, includeQuery: true, aql: "FOR x IN y ".repeat(1200) });
    const { url } = buildIssue("t", "", d);
    expect(url.length).toBeLessThanOrEqual(MAX_ISSUE_URL_LENGTH);
    expect(decodeURIComponent(url)).toContain("arangodb-schema-analyzer: 0.14.3");
    expect(decodeURIComponent(url)).toContain("This issue is public");
  });

  it("closes a code block the trim cut open, even after one the user left open", () => {
    const d = buildDetails({ ...base, includeQuery: true, aql: "FOR x IN y ".repeat(1200) });
    const decoded = decodeURIComponent(buildIssue("t", "see:\n```\nstack", d).url.split("&body=")[1]);
    const lines = decoded.split("\n");
    const noteLine = lines.findIndex((l) => l.includes("Report shortened to fit"));
    let open = false;
    for (const line of lines.slice(0, noteLine)) {
      const m = /^(`{3,})(.*)$/.exec(line);
      if (!m) continue;
      open = open ? m[2].trim() !== "" : true;
    }
    expect(open).toBe(false);
  });

  it("caps a long title so the URL stays under the limit", () => {
    const { url } = buildIssue("t".repeat(9000), "b", "d");
    expect(url.length).toBeLessThanOrEqual(MAX_ISSUE_URL_LENGTH);
    expect(decodeURIComponent(url.split("title=")[1].split("&")[0]).length).toBe(MAX_TITLE_LENGTH);
  });
});
