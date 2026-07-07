import { useCallback, useEffect, useMemo, useState } from "react";
import { getSampleQueries, type SampleQuery } from "../api/client";

interface Props {
  onSelect: (sparql: string) => void;
  onClose: () => void;
}

interface StaticSample {
  name: string;
  sparql: string;
  category: string;
}

// Hand-picked SPARQL 1.1 samples that exercise the constructs the
// translator must support first. Mirrors the Cypher UI's
// `STATIC_SAMPLES` shape but uses canonical SPARQL idioms.
const STATIC_SAMPLES: StaticSample[] = [
  {
    name: "Find all people",
    sparql:
      "PREFIX ex: <http://example.org/>\nSELECT ?person WHERE { ?person a ex:Person . }",
    category: "Basic",
  },
  {
    name: "Friends of friends",
    sparql:
      "PREFIX ex: <http://example.org/>\nSELECT DISTINCT ?fof\nWHERE {\n  ?p ex:knows ?friend .\n  ?friend ex:knows ?fof .\n}",
    category: "Traversal",
  },
  {
    name: "Optional property",
    sparql:
      "PREFIX ex: <http://example.org/>\nSELECT ?p ?email\nWHERE {\n  ?p a ex:Person .\n  OPTIONAL { ?p ex:email ?email }\n}",
    category: "Pattern",
  },
  {
    name: "Filter by literal",
    sparql:
      "PREFIX ex: <http://example.org/>\nSELECT ?p ?age\nWHERE {\n  ?p a ex:Person ; ex:age ?age .\n  FILTER(?age > 30)\n}",
    category: "Filter",
  },
  {
    name: "Count by class",
    sparql:
      "PREFIX ex: <http://example.org/>\nSELECT ?type (COUNT(?s) AS ?n)\nWHERE {\n  ?s a ?type .\n}\nGROUP BY ?type\nORDER BY DESC(?n)",
    category: "Aggregation",
  },
  {
    name: "UNION (alternative paths)",
    sparql:
      "PREFIX ex: <http://example.org/>\nSELECT ?contact\nWHERE {\n  { ?p ex:email ?contact } UNION { ?p ex:phone ?contact }\n}",
    category: "Pattern",
  },
  {
    name: "Property path",
    sparql:
      "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>\nPREFIX ex: <http://example.org/>\nSELECT ?ancestor\nWHERE { ex:alice ex:parent+ ?ancestor }",
    category: "Path",
  },
  {
    name: "ASK existence",
    sparql:
      "PREFIX ex: <http://example.org/>\nASK { ex:alice ex:knows ex:bob }",
    category: "Boolean",
  },
];

export default function SampleQueries({ onSelect, onClose }: Props) {
  const [apiQueries, setApiQueries] = useState<SampleQuery[]>([]);
  const [filter, setFilter] = useState("");
  const [dataset, setDataset] = useState<string>("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    getSampleQueries(dataset || undefined)
      .then((r) => setApiQueries(r.queries))
      .catch(() => setApiQueries([]))
      .finally(() => setLoading(false));
  }, [dataset]);

  const allItems = useMemo(() => {
    const items: { name: string; sparql: string; category: string }[] = [];
    for (const s of STATIC_SAMPLES) {
      items.push(s);
    }
    for (const q of apiQueries) {
      items.push({
        name: q.description,
        sparql: q.sparql,
        category: q.dataset,
      });
    }
    return items;
  }, [apiQueries]);

  const filtered = useMemo(() => {
    const term = filter.toLowerCase();
    return allItems.filter(
      (q) =>
        q.name.toLowerCase().includes(term) ||
        q.sparql.toLowerCase().includes(term) ||
        q.category.toLowerCase().includes(term),
    );
  }, [allItems, filter]);

  const grouped = useMemo(() => {
    const map = new Map<string, typeof filtered>();
    for (const q of filtered) {
      const list = map.get(q.category) ?? [];
      list.push(q);
      map.set(q.category, list);
    }
    return Array.from(map.entries()).sort(([a], [b]) => a.localeCompare(b));
  }, [filtered]);

  const datasets = useMemo(
    () => [...new Set(apiQueries.map((q) => q.dataset))].sort(),
    [apiQueries],
  );

  const handleSelect = useCallback(
    (sparql: string) => {
      onSelect(sparql);
      onClose();
    },
    [onSelect, onClose],
  );

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="bg-gray-900 border border-gray-700 rounded-lg shadow-2xl w-[640px] max-h-[80vh] flex flex-col">
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-800">
          <h2 className="text-sm font-semibold text-gray-100">Sample SPARQL Queries</h2>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-200 text-lg leading-none"
          >
            &times;
          </button>
        </div>

        <div className="flex items-center gap-2 px-4 py-2 border-b border-gray-800">
          <input
            type="text"
            placeholder="Search queries..."
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className="flex-1 bg-gray-800 text-gray-200 text-xs rounded px-3 py-1.5 border border-gray-700 focus:border-indigo-500 focus:outline-none"
          />
          {datasets.length > 1 && (
            <select
              value={dataset}
              onChange={(e) => setDataset(e.target.value)}
              className="bg-gray-800 text-gray-200 text-xs rounded px-2 py-1.5 border border-gray-700 focus:border-indigo-500 focus:outline-none"
            >
              <option value="">All datasets</option>
              {datasets.map((d) => (
                <option key={d} value={d}>
                  {d}
                </option>
              ))}
            </select>
          )}
        </div>

        <div className="flex-1 overflow-y-auto min-h-0">
          {loading && apiQueries.length === 0 && STATIC_SAMPLES.length === 0 ? (
            <div className="px-4 py-8 text-center text-gray-500 text-xs">
              Loading...
            </div>
          ) : grouped.length === 0 ? (
            <div className="px-4 py-8 text-center text-gray-500 text-xs">
              No queries found
            </div>
          ) : (
            grouped.map(([category, items]) => (
              <div key={category}>
                <div className="px-4 py-1.5 bg-gray-800/40 border-y border-gray-800/50 sticky top-0">
                  <span className="text-[10px] font-semibold text-gray-500 uppercase tracking-wider">
                    {category}
                  </span>
                  <span className="text-[10px] text-gray-600 ml-2">
                    ({items.length})
                  </span>
                </div>
                <ul>
                  {items.map((q, i) => (
                    <li
                      key={`${category}-${i}`}
                      className="px-4 py-2.5 hover:bg-gray-800/60 cursor-pointer transition-colors group"
                      onClick={() => handleSelect(q.sparql)}
                    >
                      <div className="flex items-center gap-2 mb-1">
                        <span className="text-xs font-medium text-gray-300 group-hover:text-gray-100">
                          {q.name}
                        </span>
                      </div>
                      <code className="text-[11px] text-indigo-400/80 block truncate">
                        {q.sparql}
                      </code>
                    </li>
                  ))}
                </ul>
              </div>
            ))
          )}
        </div>

        <div className="px-4 py-2 border-t border-gray-800 text-right">
          <span className="text-[10px] text-gray-600">
            {filtered.length} of {allItems.length} queries
          </span>
        </div>
      </div>
    </div>
  );
}
