import { useEffect, useMemo, useRef, useState } from "react";

// SPARQL clause outline. Mirrors `references/arango-cypher-py/ui/src/
// components/ClauseOutline.tsx` but parses SPARQL keywords (SELECT,
// WHERE, FILTER, OPTIONAL, UNION, MINUS, GROUP BY, ORDER BY, LIMIT,
// OFFSET, BIND, VALUES, CONSTRUCT, ASK, DESCRIBE, SERVICE).
//
// Like the Cypher version this is a regex-driven outline view —
// it's NOT a real SPARQL parser (the backend uses rdflib for that).
// It exists purely so the user can navigate a long query.

interface ClauseEntry {
  type: string;
  variables: string[];
  line: number;
  offset: number;
}

const CLAUSE_RE =
  /\b(PREFIX|BASE|SELECT|CONSTRUCT|ASK|DESCRIBE|FROM|WHERE|OPTIONAL|UNION|MINUS|FILTER|BIND|VALUES|GROUP\s+BY|ORDER\s+BY|HAVING|LIMIT|OFFSET|SERVICE|GRAPH)\b/gi;

const VAR_RE = /[?$]([a-zA-Z_]\w*)/g;

const RESERVED = new Set([
  "select", "construct", "ask", "describe", "from", "where", "optional",
  "union", "minus", "filter", "bind", "values", "group", "by", "order",
  "having", "limit", "offset", "distinct", "reduced", "as", "in", "not",
  "exists", "true", "false", "a", "service", "graph", "prefix", "base",
  "asc", "desc", "named",
]);

function extractVariables(segment: string): string[] {
  const vars = new Set<string>();
  const stripped = segment
    .replace(/'[^']*'|"[^"]*"/g, "")
    .replace(/<[^>]*>/g, ""); // strip IRIs so they don't pollute the var list
  let m: RegExpExecArray | null;
  const re = new RegExp(VAR_RE.source, "g");
  while ((m = re.exec(stripped)) !== null) {
    const name = m[1];
    if (!RESERVED.has(name.toLowerCase()) && !/^\d/.test(name)) {
      vars.add(name);
    }
  }
  return Array.from(vars);
}

function parseClauses(sparql: string): ClauseEntry[] {
  const entries: ClauseEntry[] = [];
  const re = new RegExp(CLAUSE_RE.source, "gi");
  let m: RegExpExecArray | null;
  const matchPositions: { type: string; offset: number }[] = [];

  while ((m = re.exec(sparql)) !== null) {
    matchPositions.push({
      type: m[0].replace(/\s+/g, " ").toUpperCase(),
      offset: m.index,
    });
  }

  for (let i = 0; i < matchPositions.length; i++) {
    const { type, offset } = matchPositions[i];
    const nextOffset =
      i + 1 < matchPositions.length ? matchPositions[i + 1].offset : sparql.length;
    const segment = sparql.slice(offset, nextOffset);
    const line = sparql.slice(0, offset).split("\n").length;
    entries.push({
      type,
      variables: extractVariables(segment),
      line,
      offset,
    });
  }

  return entries;
}

const CLAUSE_COLORS: Record<string, string> = {
  PREFIX: "text-gray-500",
  BASE: "text-gray-500",
  SELECT: "text-emerald-400",
  CONSTRUCT: "text-violet-400",
  ASK: "text-pink-400",
  DESCRIBE: "text-rose-400",
  FROM: "text-cyan-400",
  WHERE: "text-blue-400",
  OPTIONAL: "text-blue-300",
  UNION: "text-orange-400",
  MINUS: "text-red-400",
  FILTER: "text-amber-400",
  BIND: "text-purple-400",
  VALUES: "text-purple-300",
  "GROUP BY": "text-teal-400",
  "ORDER BY": "text-teal-300",
  HAVING: "text-amber-300",
  LIMIT: "text-gray-400",
  OFFSET: "text-gray-400",
  SERVICE: "text-indigo-300",
  GRAPH: "text-indigo-400",
};

interface Props {
  sparql: string;
  onJumpToLine?: (line: number) => void;
}

export default function ClauseOutline({ sparql, onJumpToLine }: Props) {
  const [clauses, setClauses] = useState<ClauseEntry[]>([]);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const debouncedSparql = useMemo(() => sparql, [sparql]);

  useEffect(() => {
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => {
      setClauses(parseClauses(debouncedSparql));
    }, 300);
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [debouncedSparql]);

  if (clauses.length === 0) {
    return (
      <div className="p-3 text-xs text-gray-600">
        No clauses detected. Start typing a SPARQL query.
      </div>
    );
  }

  return (
    <div className="py-1">
      {clauses.map((c, i) => (
        <button
          key={`${c.type}-${c.line}-${i}`}
          onClick={() => onJumpToLine?.(c.line)}
          className="w-full text-left px-3 py-1.5 hover:bg-gray-800/50 transition-colors group flex items-start gap-2"
        >
          <span
            className={`text-xs font-semibold shrink-0 ${CLAUSE_COLORS[c.type] || "text-gray-400"}`}
          >
            {c.type}
          </span>
          {c.variables.length > 0 && (
            <span className="text-[10px] text-gray-500 font-mono truncate">
              {c.variables.join(", ")}
            </span>
          )}
          <span className="ml-auto text-[10px] text-gray-700 shrink-0 group-hover:text-gray-500">
            L{c.line}
          </span>
        </button>
      ))}
    </div>
  );
}
