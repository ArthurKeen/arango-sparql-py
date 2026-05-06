import { useCallback, useMemo, useState, type ReactNode } from "react";
import type { Action, ResultTab } from "../api/store";
import CytoscapeGraph from "./CytoscapeGraph";
import type { CyNode } from "./CytoscapeGraph";

// SPARQL results panel. Mirrors `references/arango-cypher-py/ui/src/
// components/ResultsPanel.tsx` but trimmed: no Explain / Profile tabs
// (the SPARQL backend has not landed those endpoints yet), and the
// graph tab passes `bindings={results}` straight to CytoscapeGraph
// so the RDF-literal-as-property collapse rule applies.

interface Props {
  results: unknown[] | null;
  warnings: Array<{ message: string }>;
  activeTab: ResultTab;
  dispatch: (action: Action) => void;
  execMs?: number | null;
}

const ALWAYS_TABS: { id: ResultTab; label: string }[] = [
  { id: "table", label: "Table" },
  { id: "json", label: "JSON" },
  { id: "graph", label: "Graph" },
];

const SENTINEL_STRINGS = new Set([
  "NULL", "NONE", "NIL", "N/A", "NA", "UNKNOWN",
  "TBD", "TBA", "#N/A", "(NULL)",
]);

function isSentinelString(v: unknown): v is string {
  return typeof v === "string" && SENTINEL_STRINGS.has(v.trim().toUpperCase());
}

function renderCellValue(val: unknown): ReactNode {
  if (val === null || val === undefined) {
    return <span className="text-gray-600 italic">null</span>;
  }
  if (isSentinelString(val)) {
    return (
      <span
        className="text-amber-400/80"
        title="String sentinel value, not a real null. Filter it out in your query to exclude these rows."
      >
        &ldquo;{val}&rdquo;
      </span>
    );
  }
  // SPARQL results JSON term: { type, value, datatype?, "xml:lang"? }.
  // Display the `value` and tint typed/lang-tagged literals so the
  // user can see they came back as something other than a bare
  // string.
  if (val && typeof val === "object" && "value" in (val as Record<string, unknown>)) {
    const term = val as { type?: string; value?: unknown; datatype?: string; "xml:lang"?: string };
    const display = String(term.value ?? "");
    const tag = term["xml:lang"] ? `@${term["xml:lang"]}` : "";
    return (
      <span title={term.datatype ? `^^${term.datatype}` : term.type ?? ""}>
        {display}
        {tag && <span className="text-gray-500 ml-1">{tag}</span>}
      </span>
    );
  }
  if (typeof val === "object") {
    return <span className="text-xs">{JSON.stringify(val)}</span>;
  }
  return String(val);
}

function TableView({ data }: { data: unknown[] }) {
  if (data.length === 0) {
    return (
      <div className="p-4 text-gray-500 text-sm">No results returned.</div>
    );
  }

  const allKeys = new Set<string>();
  for (const row of data) {
    if (row && typeof row === "object" && !Array.isArray(row)) {
      Object.keys(row).forEach((k) => allKeys.add(k));
    }
  }
  const columns = Array.from(allKeys);

  if (columns.length === 0) {
    return (
      <div className="p-4 overflow-auto">
        {data.map((item, i) => (
          <div key={i} className="text-sm text-gray-300 mb-1 font-mono">
            {JSON.stringify(item)}
          </div>
        ))}
      </div>
    );
  }

  return (
    <div className="overflow-auto h-full">
      <table className="w-full text-sm text-left">
        <thead className="sticky top-0 bg-gray-800 text-gray-400 text-xs uppercase">
          <tr>
            <th className="px-3 py-2 font-medium text-gray-500 w-10">#</th>
            {columns.map((col) => (
              <th key={col} className="px-3 py-2 font-medium">
                {col}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.map((row, i) => (
            <tr
              key={i}
              className="border-t border-gray-800 hover:bg-gray-800/50"
            >
              <td className="px-3 py-1.5 text-gray-500 font-mono text-xs">
                {i + 1}
              </td>
              {columns.map((col) => {
                const val =
                  row && typeof row === "object" && !Array.isArray(row)
                    ? (row as Record<string, unknown>)[col]
                    : undefined;
                return (
                  <td key={col} className="px-3 py-1.5 text-gray-300 font-mono">
                    {renderCellValue(val)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function JsonView({ data }: { data: unknown }) {
  return (
    <pre className="p-4 text-sm text-gray-300 font-mono overflow-auto h-full whitespace-pre-wrap">
      {JSON.stringify(data, null, 2)}
    </pre>
  );
}

function NodeInspector({
  node,
  onClose,
}: {
  node: CyNode;
  onClose: () => void;
}) {
  return (
    <div className="w-60 border-l border-gray-800 overflow-auto p-3 bg-gray-900/50 shrink-0">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2 min-w-0">
          <span
            className="w-3 h-3 rounded-full shrink-0"
            style={{ backgroundColor: node.color || "#6366f1" }}
          />
          <span className="text-sm font-semibold text-white truncate">
            {node.label}
          </span>
        </div>
        <button
          onClick={onClose}
          className="text-[10px] text-gray-500 hover:text-gray-300 transition-colors shrink-0 ml-2"
        >
          Close
        </button>
      </div>
      <div className="text-[10px] text-gray-500 mb-2 font-mono break-all">{node.id}</div>
      <div className="space-y-1">
        {Object.entries(node.data)
          .filter(
            ([k]) =>
              !k.startsWith("_") &&
              k !== "id" &&
              k !== "label" &&
              k !== "color",
          )
          .map(([k, v]) => (
            <div key={k} className="flex items-start gap-2">
              <span className="text-xs text-gray-400 shrink-0 font-mono">
                {k}:
              </span>
              <span className="text-xs text-gray-300 font-mono break-all">
                {renderCellValue(v)}
              </span>
            </div>
          ))}
      </div>
    </div>
  );
}

function GraphView({ data }: { data: unknown[] }) {
  const [selected, setSelected] = useState<CyNode | null>(null);

  // Pass `bindings` directly. CytoscapeGraph's deriveGraph() handles
  // both s/p/o triple shapes and generic projection shapes, plus the
  // collapse / expand toggle for literals.
  const bindings = useMemo(
    () =>
      (data ?? []).filter(
        (row): row is Record<string, never> =>
          row != null && typeof row === "object" && !Array.isArray(row),
      ) as Array<Record<string, never>>,
    [data],
  );

  if (bindings.length === 0) {
    return (
      <div className="flex items-center justify-center h-full">
        <p className="text-gray-500 text-sm">
          No graph data to render. Project IRIs (e.g.{" "}
          <code className="text-gray-400">?s ?p ?o</code>) to see
          relationships here.
        </p>
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col">
      <div className="flex items-center gap-4 px-3 py-1 shrink-0">
        <span className="text-xs text-gray-400">
          {bindings.length} binding{bindings.length === 1 ? "" : "s"}
        </span>
        {selected && (
          <button
            onClick={() => setSelected(null)}
            className="ml-auto text-[10px] text-gray-500 hover:text-gray-300 transition-colors"
          >
            Clear selection
          </button>
        )}
      </div>
      <div className="flex-1 min-h-0 flex">
        <div className={selected ? "flex-1 min-w-0" : "w-full"}>
          <CytoscapeGraph
            bindings={bindings}
            onNodeClick={setSelected}
            onBackgroundClick={() => setSelected(null)}
          />
        </div>
        {selected && (
          <NodeInspector node={selected} onClose={() => setSelected(null)} />
        )}
      </div>
    </div>
  );
}

function WarningsBanner({ warnings }: { warnings: Array<{ message: string }> }) {
  if (warnings.length === 0) return null;
  return (
    <div className="px-3 py-1.5 bg-amber-900/20 border-b border-amber-800/30 flex items-start gap-2">
      <span className="text-amber-500 text-xs mt-0.5">&#9888;</span>
      <div className="flex-1">
        {warnings.map((w, i) => (
          <p key={i} className="text-xs text-amber-400">
            {w.message}
          </p>
        ))}
      </div>
    </div>
  );
}

function downloadBlob(content: string, filename: string, mime: string) {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

function toCsv(data: unknown[]): string {
  if (data.length === 0) return "";
  const allKeys = new Set<string>();
  for (const row of data) {
    if (row && typeof row === "object" && !Array.isArray(row)) {
      Object.keys(row).forEach((k) => allKeys.add(k));
    }
  }
  const columns = Array.from(allKeys);
  if (columns.length === 0) {
    return data.map((item) => JSON.stringify(item)).join("\n");
  }
  const escape = (v: unknown) => {
    const s =
      v === null || v === undefined
        ? ""
        : typeof v === "object"
          ? JSON.stringify(v)
          : String(v);
    return s.includes(",") || s.includes('"') || s.includes("\n")
      ? `"${s.replace(/"/g, '""')}"`
      : s;
  };
  const header = columns.map(escape).join(",");
  const rows = data.map((row) => {
    const obj =
      row && typeof row === "object" && !Array.isArray(row)
        ? (row as Record<string, unknown>)
        : {};
    return columns.map((col) => escape(obj[col])).join(",");
  });
  return [header, ...rows].join("\n");
}

export default function ResultsPanel({
  results,
  warnings,
  activeTab,
  dispatch,
  execMs,
}: Props) {
  const handleExportJson = useCallback(() => {
    if (!results) return;
    downloadBlob(
      JSON.stringify(results, null, 2),
      "results.json",
      "application/json",
    );
  }, [results]);

  const handleExportCsv = useCallback(() => {
    if (!results) return;
    downloadBlob(toCsv(results), "results.csv", "text/csv");
  }, [results]);

  const tabs = ALWAYS_TABS;

  return (
    <div className="h-full flex flex-col">
      <div className="flex border-b border-gray-700 bg-gray-900/50">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            onClick={() => dispatch({ type: "SET_RESULT_TAB", tab: tab.id })}
            className={`px-4 py-2 text-xs font-medium transition-colors ${
              activeTab === tab.id
                ? "text-indigo-400 border-b-2 border-indigo-400"
                : "text-gray-500 hover:text-gray-300"
            }`}
          >
            {tab.label}
          </button>
        ))}
        {results && results.length > 0 && (
          <div className="ml-auto flex items-center gap-1.5 px-2">
            <button
              onClick={handleExportCsv}
              className="px-2 py-1 text-[10px] rounded bg-gray-800 text-gray-400 hover:text-gray-200 transition-colors"
              title="Download as CSV"
            >
              CSV
            </button>
            <button
              onClick={handleExportJson}
              className="px-2 py-1 text-[10px] rounded bg-gray-800 text-gray-400 hover:text-gray-200 transition-colors"
              title="Download as JSON"
            >
              JSON
            </button>
            <span className="text-xs text-gray-500 ml-1">
              {results.length} row{results.length !== 1 ? "s" : ""}
              {execMs != null && (
                <span className="text-sky-400/70 ml-1.5 tabular-nums">
                  {execMs}ms
                </span>
              )}
            </span>
          </div>
        )}
      </div>

      <WarningsBanner warnings={warnings} />

      <div className="flex-1 min-h-0 overflow-auto">
        {results === null ? (
          <div className="flex items-center justify-center h-full">
            <p className="text-gray-600 text-sm">
              Run a query to see results here.
            </p>
          </div>
        ) : activeTab === "table" ? (
          <TableView data={results} />
        ) : activeTab === "json" ? (
          <JsonView data={results} />
        ) : activeTab === "graph" ? (
          <GraphView data={results} />
        ) : (
          <div className="flex items-center justify-center h-full">
            <p className="text-gray-600 text-sm">No data for this view yet.</p>
          </div>
        )}
      </div>
    </div>
  );
}
