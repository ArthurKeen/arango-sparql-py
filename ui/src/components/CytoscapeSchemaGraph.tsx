import { useEffect, useRef } from "react";
import cytoscape from "cytoscape";
import type { Core } from "cytoscape";
import type { OwlClass, OwlProperty } from "../api/client";

// Cytoscape renderer for the OWL schema. Mirrors the Cypher UI's
// `CytoscapeSchemaGraph` in role (visualize classes + edges of the
// active schema), but consumes the SPARQL-flavored shape:
//
//   * `OwlClass` → a node, labelled by `localName`
//   * `OwlProperty` (kind = "object") → an edge from each domain class
//     to each range class
//   * `OwlProperty` (kind = "datatype" | "annotation") → a property
//     bag glyph attached to the domain class (we render literals as
//     properties, never as nodes — same RDF-collapse rule as
//     `CytoscapeGraph.tsx`).
//
// Until `/schema/owl` is wired up this component will receive empty
// arrays and the parent (`SchemaGraph.tsx`) will short-circuit to a
// placeholder. The component is included for layout symmetry with
// the Cypher UI and so the future wire-up only flips data sources.

interface Props {
  classes: OwlClass[];
  properties: OwlProperty[];
}

const NODE_COLORS = [
  "#6366f1", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6",
  "#06b6d4", "#ec4899", "#84cc16",
];

export default function CytoscapeSchemaGraph({ classes, properties }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<Core | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    const colorByClass = new Map<string, string>();
    classes.forEach((c, i) => {
      colorByClass.set(c.iri, NODE_COLORS[i % NODE_COLORS.length]);
    });

    const datatypeProps = new Map<string, string[]>();
    for (const p of properties) {
      if (p.kind === "datatype" || p.kind === "annotation") {
        for (const dom of p.domain) {
          const list = datatypeProps.get(dom) ?? [];
          list.push(p.localName);
          datatypeProps.set(dom, list);
        }
      }
    }

    const cyNodes = classes.map((c) => ({
      data: {
        id: c.iri,
        label: c.localName,
        color: colorByClass.get(c.iri) ?? NODE_COLORS[0],
        properties: datatypeProps.get(c.iri) ?? [],
        comment: c.comment ?? "",
      },
    }));

    const cyEdges: cytoscape.ElementDefinition[] = [];
    let edgeIdx = 0;
    for (const p of properties) {
      if (p.kind !== "object") continue;
      for (const from of p.domain) {
        for (const to of p.range) {
          cyEdges.push({
            data: {
              id: `edge-${edgeIdx++}`,
              source: from,
              target: to,
              label: p.localName,
            },
          });
        }
      }
    }

    const cy = cytoscape({
      container: containerRef.current,
      elements: [...cyNodes, ...cyEdges],
      layout: {
        name: "cose",
        animate: false,
        nodeRepulsion: () => 8000,
        idealEdgeLength: () => 120,
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
            width: 1.5,
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
      ],
      minZoom: 0.15,
      maxZoom: 5,
      wheelSensitivity: 0.3,
    });

    cyRef.current = cy;

    return () => {
      cy.destroy();
      cyRef.current = null;
    };
  }, [classes, properties]);

  return (
    <div className="relative w-full h-full">
      <div
        ref={containerRef}
        className="w-full h-full bg-gray-900 rounded border border-gray-800"
      />
    </div>
  );
}
