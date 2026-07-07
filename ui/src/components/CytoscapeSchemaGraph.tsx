import { useEffect, useMemo, useRef, useState } from "react";
import cytoscape from "cytoscape";
import type { Core } from "cytoscape";
import type { OwlClass, OwlProperty } from "../api/client";
import {
  buildSchemaModel,
  bundleLabel,
  edgeWidth,
  matchSchema,
  neighborsOf,
  type SchemaBundle,
  type SchemaGraphModel,
  type SchemaGraphNode,
} from "../utils/schemaGraph";

// Cytoscape renderer for the OWL schema (WP-UI-GRAPH, PRD §10.18).
//
//   * Classes → nodes (labelled by localName); literals render as a
//     property bag on their domain class, never as nodes.
//   * Object properties sharing a (domain, range) pair collapse into one
//     bundled arc; the arc label shows "pred +N" and clicking it expands
//     the member predicates in a side panel.
//   * Arc thickness reflects instance volume when analyzer statistics are
//     supplied (`counts`), else every arc renders at the base width.
//   * A search box highlights class/predicate matches and dims the rest;
//     clicking a class focuses it + its neighbours.

interface Props {
  classes: OwlClass[];
  properties: OwlProperty[];
  /** Per-relationship instance counts (localName or IRI keyed). */
  counts?: Record<string, number>;
}

const NODE_COLORS = [
  "#6366f1", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6",
  "#06b6d4", "#ec4899", "#84cc16",
];

type Selection =
  | { kind: "bundle"; bundle: SchemaBundle; sourceLabel: string; targetLabel: string }
  | { kind: "node"; node: SchemaGraphNode; neighbors: number }
  | null;

export default function CytoscapeSchemaGraph({ classes, properties, counts }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<Core | null>(null);
  const modelRef = useRef<SchemaGraphModel | null>(null);

  const [query, setQuery] = useState("");
  const [focusNode, setFocusNode] = useState<string | null>(null);
  const [selection, setSelection] = useState<Selection>(null);

  const model = useMemo(
    () => buildSchemaModel(classes, properties, counts ?? {}),
    [classes, properties, counts],
  );
  modelRef.current = model;

  const labelById = useMemo(() => {
    const m = new Map<string, string>();
    for (const n of model.nodes) m.set(n.id, n.label);
    return m;
  }, [model]);

  const weighted = model.maxBundleCount > 0;

  // Build (and rebuild) the cytoscape instance when the graph data changes.
  useEffect(() => {
    if (!containerRef.current) return;

    const colorByClass = new Map<string, string>();
    model.nodes.forEach((n, i) => {
      colorByClass.set(n.id, NODE_COLORS[i % NODE_COLORS.length]);
    });

    const cyNodes = model.nodes.map((n) => ({
      data: {
        id: n.id,
        label: n.datatypeProps.length
          ? `${n.label}  •${n.datatypeProps.length}`
          : n.label,
        color: colorByClass.get(n.id) ?? NODE_COLORS[0],
      },
    }));

    const cyEdges: cytoscape.ElementDefinition[] = model.bundles.map((b) => ({
      data: {
        id: b.id,
        source: b.source,
        target: b.target,
        label: bundleLabel(b),
        width: edgeWidth(b.count, model.maxBundleCount),
        bundled: b.members.length > 1,
      },
    }));

    const cy = cytoscape({
      container: containerRef.current,
      elements: [...cyNodes, ...cyEdges],
      layout: {
        name: "cose",
        animate: false,
        nodeRepulsion: () => 8000,
        idealEdgeLength: () => 130,
        gravity: 0.3,
        padding: 40,
      } as cytoscape.LayoutOptions,
      style: [
        {
          selector: "node",
          style: {
            label: "data(label)",
            "background-color": "data(color)",
            "text-valign": "bottom",
            "text-halign": "center",
            "font-size": "10px",
            color: "#d1d5db",
            "text-margin-y": 6,
            width: 36,
            height: 36,
            "border-width": 2,
            "border-color": "data(color)",
            "border-opacity": 0.4,
          } as cytoscape.Css.Node,
        },
        {
          selector: "edge",
          style: {
            width: "data(width)",
            "line-color": "#4b5563",
            "target-arrow-color": "#4b5563",
            "target-arrow-shape": "triangle",
            "curve-style": "bezier",
            label: "data(label)",
            "font-size": "9px",
            color: "#9ca3af",
            "text-background-color": "#111827",
            "text-background-opacity": 0.85,
            "text-background-padding": "3px",
            "text-rotation": "autorotate",
          } as cytoscape.Css.Edge,
        },
        {
          selector: "edge[?bundled]",
          style: { "line-style": "solid", "line-color": "#6b7280" } as cytoscape.Css.Edge,
        },
        {
          selector: ".dim",
          style: { opacity: 0.12, "text-opacity": 0.12 } as cytoscape.Css.Node,
        },
        {
          selector: "node.match",
          style: {
            "border-width": 4,
            "border-color": "#fbbf24",
            "border-opacity": 1,
          } as cytoscape.Css.Node,
        },
        {
          selector: "edge.match",
          style: { "line-color": "#fbbf24", "target-arrow-color": "#fbbf24" } as cytoscape.Css.Edge,
        },
      ],
      minZoom: 0.15,
      maxZoom: 5,
    });

    cy.on("tap", "edge", (evt) => {
      const b = modelRef.current?.bundles.find((x) => x.id === evt.target.id());
      if (b) {
        setSelection({
          kind: "bundle",
          bundle: b,
          sourceLabel: labelById.get(b.source) ?? b.source,
          targetLabel: labelById.get(b.target) ?? b.target,
        });
      }
    });

    cy.on("tap", "node", (evt) => {
      const m = modelRef.current;
      const node = m?.nodes.find((x) => x.id === evt.target.id());
      if (node && m) {
        setFocusNode(node.id);
        setSelection({ kind: "node", node, neighbors: neighborsOf(node.id, m).size });
      }
    });

    cy.on("tap", (evt) => {
      if (evt.target === cy) {
        setFocusNode(null);
        setSelection(null);
      }
    });

    cyRef.current = cy;
    return () => {
      cy.destroy();
      cyRef.current = null;
    };
  }, [model, labelById]);

  // Apply search highlight / node focus without rebuilding the graph
  // (PRD §10.18 — paint on the stable layout, never relayout).
  useEffect(() => {
    const cy = cyRef.current;
    const m = modelRef.current;
    if (!cy || !m) return;

    cy.batch(() => {
      cy.elements().removeClass("dim match");

      let focus: Set<string> | null = null;
      const matchNodes = new Set<string>();
      const matchBundles = new Set<string>();

      if (focusNode) {
        focus = neighborsOf(focusNode, m);
        focus.add(focusNode);
        matchNodes.add(focusNode);
      } else {
        const r = matchSchema(query, m);
        if (r.active) {
          focus = r.focusNodeIds;
          r.matchedNodeIds.forEach((id) => matchNodes.add(id));
          r.matchedBundleIds.forEach((id) => matchBundles.add(id));
        }
      }

      if (!focus) return;

      cy.nodes().forEach((n) => {
        if (!focus!.has(n.id())) n.addClass("dim");
      });
      cy.edges().forEach((e) => {
        const inFocus = focus!.has(e.source().id()) && focus!.has(e.target().id());
        if (!inFocus) e.addClass("dim");
      });
      matchNodes.forEach((id) => cy.getElementById(id).addClass("match"));
      matchBundles.forEach((id) => cy.getElementById(id).addClass("match"));
    });
  }, [query, focusNode, model]);

  const empty = model.nodes.length === 0;

  return (
    <div className="relative w-full h-full">
      <div
        ref={containerRef}
        className="w-full h-full bg-gray-900 rounded border border-gray-800"
      />

      {/* Search box */}
      <div className="absolute top-2 left-2 flex items-center gap-2">
        <input
          type="text"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setFocusNode(null);
          }}
          placeholder="Search classes / properties…"
          className="w-56 bg-gray-800/90 text-gray-200 text-xs rounded px-2 py-1 border border-gray-700 focus:border-indigo-500 focus:outline-none"
        />
        {(query || focusNode) && (
          <button
            onClick={() => {
              setQuery("");
              setFocusNode(null);
              setSelection(null);
            }}
            className="text-[10px] px-2 py-1 rounded border border-gray-700 text-gray-400 hover:text-gray-200"
          >
            Clear
          </button>
        )}
      </div>

      {/* Legend */}
      {!empty && (
        <div className="absolute bottom-2 left-2 bg-gray-900/85 border border-gray-800 rounded px-2.5 py-1.5 text-[10px] text-gray-400 leading-relaxed max-w-[240px]">
          <div><span className="text-gray-300">Node</span> = class · <span className="text-gray-300">•N</span> = datatype props</div>
          <div><span className="text-gray-300">Arc</span> = object property · <span className="text-gray-300">+N</span> = bundled predicates (click to expand)</div>
          <div>
            <span className="text-gray-300">Thickness</span> ={" "}
            {weighted ? "instance volume" : "uniform (no stats)"}
          </div>
        </div>
      )}

      {/* Selection / expand panel */}
      {selection && (
        <div className="absolute top-2 right-2 w-64 bg-gray-900 border border-gray-700 rounded-lg shadow-2xl text-xs">
          <div className="flex items-center justify-between px-3 py-2 border-b border-gray-800">
            <span className="font-medium text-gray-200">
              {selection.kind === "bundle"
                ? `${selection.sourceLabel} → ${selection.targetLabel}`
                : selection.node.label}
            </span>
            <button
              onClick={() => setSelection(null)}
              className="text-gray-500 hover:text-gray-300 text-sm leading-none"
            >
              &times;
            </button>
          </div>
          <div className="p-3 max-h-56 overflow-y-auto">
            {selection.kind === "bundle" ? (
              <ul className="space-y-1">
                {selection.bundle.members.map((mem) => (
                  <li key={mem.iri} className="flex items-center justify-between gap-2">
                    <code className="text-indigo-300">{mem.localName}</code>
                    {typeof mem.count === "number" && mem.count > 0 && (
                      <span className="text-gray-500 tabular-nums">{mem.count.toLocaleString()}</span>
                    )}
                  </li>
                ))}
              </ul>
            ) : (
              <div className="space-y-2">
                <div className="text-gray-500">
                  {selection.neighbors} connected class
                  {selection.neighbors === 1 ? "" : "es"} · focused
                </div>
                {selection.node.datatypeProps.length > 0 && (
                  <div>
                    <div className="text-gray-500 mb-1">Datatype properties</div>
                    <ul className="space-y-0.5">
                      {selection.node.datatypeProps.map((p) => (
                        <li key={p}>
                          <code className="text-emerald-300">{p}</code>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {selection.node.comment && (
                  <div className="text-gray-400 border-t border-gray-800 pt-2">
                    {selection.node.comment}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
