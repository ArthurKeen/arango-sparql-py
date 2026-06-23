// Named-graph scope picker. Mirrors arango-cypher-py's GraphSelector:
// a header pill that restricts schema acquisition (and the OWL view / NL
// suggestions derived from it) to one ArangoDB named graph's collections.
// `null` selection means "all collections" (the default). Hidden when the
// connected database has no named graphs.

import type { GraphInfo } from "../api/client";

interface Props {
  graphs: GraphInfo[];
  selection: string | null;
  loading: boolean;
  onSelect: (graphName: string | null) => void;
  error?: string | null;
}

export default function GraphSelector({
  graphs,
  selection,
  loading,
  onSelect,
  error,
}: Props) {
  // Nothing to scope to and no error to report → stay out of the way.
  if (!loading && graphs.length === 0 && !error) return null;

  const scoped = selection != null;
  const tooltip = (() => {
    if (error) return `error: ${error}`;
    if (scoped) {
      const g = graphs.find((x) => x.name === selection);
      return g
        ? `Scoped to "${g.name}" — ${g.collectionCount} collection(s)`
        : `Scoped to "${selection}"`;
    }
    return "Schema covers all collections; pick a graph to narrow it";
  })();

  return (
    <div
      className={`flex items-center gap-1.5 px-2 py-1 rounded border text-xs ${
        scoped
          ? "bg-sky-900/20 border-sky-700/40"
          : "bg-gray-800/60 border-gray-700"
      }`}
      title={tooltip}
    >
      <span className={scoped ? "text-sky-400" : "text-gray-400"}>Graph:</span>
      <select
        value={selection ?? ""}
        onChange={(e) => onSelect(e.target.value || null)}
        disabled={loading || graphs.length === 0}
        className={`bg-gray-800 text-xs rounded px-1.5 py-0.5 focus:outline-none ${
          scoped
            ? "border border-sky-700/40 text-sky-200 focus:border-sky-500"
            : "border border-gray-700 text-gray-200 focus:border-gray-500"
        }`}
      >
        <option value="">All collections</option>
        {graphs.map((g) => (
          <option key={g.name} value={g.name}>
            {g.name} ({g.collectionCount})
          </option>
        ))}
      </select>
      {loading && (
        <span className="text-[10px] text-gray-500">updating…</span>
      )}
    </div>
  );
}
