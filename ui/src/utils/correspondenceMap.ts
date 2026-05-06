// Best-effort line correspondence between SPARQL source and the
// translated AQL. Mirrors `references/arango-cypher-py/ui/src/utils/
// correspondenceMap.ts` but with a SPARQL clause → AQL keyword map.
//
// This is intentionally regex-driven and approximate — the goal is
// "hover SPARQL line, dim the corresponding AQL line(s)", not formal
// provenance. Real provenance, if/when we want it, lives in the
// translator output.

const SPARQL_CLAUSE_RE =
  /^\s*(SELECT|CONSTRUCT|ASK|DESCRIBE|FROM|WHERE|OPTIONAL|UNION|MINUS|FILTER|BIND|VALUES|GROUP\s+BY|ORDER\s+BY|HAVING|LIMIT|OFFSET|SERVICE|GRAPH|PREFIX|BASE)\b/i;

const AQL_KEYWORD_MAP: Record<string, string[]> = {
  SELECT: ["RETURN"],
  CONSTRUCT: ["RETURN"],
  ASK: ["RETURN"],
  DESCRIBE: ["RETURN"],
  WHERE: ["FOR", "FILTER", "LET"],
  OPTIONAL: ["LET"],
  UNION: ["FOR"],
  MINUS: ["FILTER"],
  FILTER: ["FILTER"],
  BIND: ["LET"],
  VALUES: ["LET", "FOR"],
  "GROUP BY": ["COLLECT"],
  "ORDER BY": ["SORT"],
  HAVING: ["FILTER"],
  LIMIT: ["LIMIT"],
  OFFSET: ["LIMIT"],
  GRAPH: ["FOR"],
  SERVICE: ["FOR"],
};

interface ClauseRange {
  clauseType: string;
  startLine: number;
  endLine: number;
}

function identifySparqlClauses(sparql: string): ClauseRange[] {
  const lines = sparql.split("\n");
  const ranges: ClauseRange[] = [];

  for (let i = 0; i < lines.length; i++) {
    const m = lines[i].match(SPARQL_CLAUSE_RE);
    if (m) {
      const clauseType = m[1].replace(/\s+/g, " ").toUpperCase();
      if (ranges.length > 0) {
        ranges[ranges.length - 1].endLine = i - 1;
      }
      ranges.push({ clauseType, startLine: i, endLine: i });
    }
  }
  if (ranges.length > 0) {
    ranges[ranges.length - 1].endLine = sparql.split("\n").length - 1;
  }

  return ranges;
}

export function buildCorrespondenceMap(
  sparql: string,
  aql: string,
): Map<number, number[]> {
  const result = new Map<number, number[]>();
  if (!sparql.trim() || !aql.trim()) return result;

  const clauses = identifySparqlClauses(sparql);
  const aqlLines = aql.split("\n");

  const aqlLineKeywords: string[] = aqlLines.map((line) => {
    const trimmed = line.trim().toUpperCase();
    const kw = trimmed.split(/[\s(]/)[0];
    return kw || "";
  });

  for (const clause of clauses) {
    const mappedAqlKeywords = AQL_KEYWORD_MAP[clause.clauseType] || [];
    const matchedAqlLines: number[] = [];

    for (let a = 0; a < aqlLineKeywords.length; a++) {
      if (mappedAqlKeywords.includes(aqlLineKeywords[a])) {
        matchedAqlLines.push(a);
      }
    }

    for (let cl = clause.startLine; cl <= clause.endLine; cl++) {
      const existing = result.get(cl) || [];
      result.set(cl, [...existing, ...matchedAqlLines]);
    }
  }

  return result;
}

export function buildReverseMap(
  forward: Map<number, number[]>,
): Map<number, number[]> {
  const reverse = new Map<number, number[]>();
  for (const [sparqlLine, aqlLines] of forward) {
    for (const aqlLine of aqlLines) {
      const existing = reverse.get(aqlLine) || [];
      if (!existing.includes(sparqlLine)) {
        existing.push(sparqlLine);
      }
      reverse.set(aqlLine, existing);
    }
  }
  return reverse;
}
