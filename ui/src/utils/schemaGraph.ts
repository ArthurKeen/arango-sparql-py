// Schema-graph scalability model (WP-UI-GRAPH, PRD §10.18).
//
// Pure, render-agnostic transforms over the OWL `{classes, properties}`
// shape so `CytoscapeSchemaGraph` stays thin and the scaling behaviours
// (bundling, weighting, search/focus) are unit-testable without a DOM:
//
//   * Relationship bundling — object properties sharing the same
//     (domain, range) class pair collapse into one arc whose `members`
//     carry the individual predicates (expandable on click).
//   * Edge-volume weighting — `edgeWidth` maps an instance count to a
//     log-scaled stroke width; counts come from analyzer statistics when
//     available, else every arc renders at the base width.
//   * Search / focus — `matchSchema` returns the matched + focus
//     (matched ∪ neighbours) sets so the renderer can highlight matches
//     and dim the rest.

import type { OwlClass, OwlProperty } from "../api/client";

export interface SchemaGraphNode {
  id: string;
  label: string;
  datatypeProps: string[];
  comment?: string;
}

export interface SchemaBundleMember {
  iri: string;
  localName: string;
  count?: number;
}

export interface SchemaBundle {
  id: string;
  source: string;
  target: string;
  members: SchemaBundleMember[];
  /** Summed instance count across members (0 when no stats available). */
  count: number;
}

export interface SchemaGraphModel {
  nodes: SchemaGraphNode[];
  bundles: SchemaBundle[];
  /** Largest bundle count — the denominator for width scaling. */
  maxBundleCount: number;
}

const BUNDLE_SEP = "\u0000";

/** Stable key for the (source → target) directed class pair. */
export function bundleKey(source: string, target: string): string {
  return `${source}${BUNDLE_SEP}${target}`;
}

function localOf(iri: string): string {
  const hash = iri.lastIndexOf("#");
  const slash = iri.lastIndexOf("/");
  const cut = Math.max(hash, slash);
  return cut >= 0 && cut < iri.length - 1 ? iri.slice(cut + 1) : iri;
}

/**
 * Build the bundled schema model. Object properties become directed
 * bundles between class nodes; datatype/annotation properties attach to
 * their domain class as a property bag. Endpoints without a class node are
 * skipped (Cytoscape throws on dangling edges).
 */
export function buildSchemaModel(
  classes: OwlClass[],
  properties: OwlProperty[],
  counts: Record<string, number> = {},
): SchemaGraphModel {
  const nodeIds = new Set(classes.map((c) => c.iri));

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

  const nodes: SchemaGraphNode[] = classes.map((c) => ({
    id: c.iri,
    label: c.localName,
    datatypeProps: datatypeProps.get(c.iri) ?? [],
    comment: c.comment,
  }));

  const byKey = new Map<string, SchemaBundle>();
  for (const p of properties) {
    if (p.kind !== "object") continue;
    const count = counts[p.localName] ?? counts[p.iri];
    for (const from of p.domain) {
      if (!nodeIds.has(from)) continue;
      for (const to of p.range) {
        if (!nodeIds.has(to)) continue;
        const key = bundleKey(from, to);
        let bundle = byKey.get(key);
        if (!bundle) {
          bundle = { id: key, source: from, target: to, members: [], count: 0 };
          byKey.set(key, bundle);
        }
        bundle.members.push({ iri: p.iri, localName: p.localName, count });
        if (typeof count === "number" && Number.isFinite(count)) {
          bundle.count += count;
        }
      }
    }
  }

  const bundles = [...byKey.values()];
  const maxBundleCount = bundles.reduce((m, b) => Math.max(m, b.count), 0);
  return { nodes, bundles, maxBundleCount };
}

export const MIN_EDGE_WIDTH = 1.5;
export const MAX_EDGE_WIDTH = 9;

/**
 * Log-scaled stroke width for an edge carrying `count` instances, relative
 * to the busiest edge (`maxCount`). Returns the base width when no counts
 * are available so an unweighted graph looks uniform rather than hairline.
 */
export function edgeWidth(
  count: number,
  maxCount: number,
  min = MIN_EDGE_WIDTH,
  max = MAX_EDGE_WIDTH,
): number {
  if (maxCount <= 0 || count <= 0) return min;
  const scale = Math.log1p(count) / Math.log1p(maxCount);
  return min + (max - min) * Math.min(1, Math.max(0, scale));
}

/** A short arc label: first member + "+N" when the pair is bundled. */
export function bundleLabel(bundle: SchemaBundle): string {
  if (bundle.members.length === 0) return "";
  const head = bundle.members[0].localName;
  const extra = bundle.members.length - 1;
  return extra > 0 ? `${head} +${extra}` : head;
}

export interface SchemaMatch {
  active: boolean;
  matchedNodeIds: Set<string>;
  matchedBundleIds: Set<string>;
  /** Matched nodes plus their immediate neighbours (the focus subgraph). */
  focusNodeIds: Set<string>;
}

/**
 * Compute the search highlight/focus sets for `query`. Matches class
 * labels/IRIs and predicate local names; the focus set adds the immediate
 * neighbours of matched nodes so a match keeps its local context on screen
 * while everything else dims.
 */
export function matchSchema(query: string, model: SchemaGraphModel): SchemaMatch {
  const q = query.trim().toLowerCase();
  const matchedNodeIds = new Set<string>();
  const matchedBundleIds = new Set<string>();
  const focusNodeIds = new Set<string>();
  if (!q) {
    return { active: false, matchedNodeIds, matchedBundleIds, focusNodeIds };
  }

  for (const n of model.nodes) {
    if (n.label.toLowerCase().includes(q) || n.id.toLowerCase().includes(q)) {
      matchedNodeIds.add(n.id);
    }
  }

  for (const b of model.bundles) {
    const hit = b.members.some((m) => m.localName.toLowerCase().includes(q));
    if (hit) {
      matchedBundleIds.add(b.id);
      matchedNodeIds.add(b.source);
      matchedNodeIds.add(b.target);
    }
  }

  for (const id of matchedNodeIds) focusNodeIds.add(id);
  for (const b of model.bundles) {
    if (matchedNodeIds.has(b.source)) focusNodeIds.add(b.target);
    if (matchedNodeIds.has(b.target)) focusNodeIds.add(b.source);
  }

  return { active: true, matchedNodeIds, matchedBundleIds, focusNodeIds };
}

/** Nodes directly connected to `nodeId` through any bundle (either direction). */
export function neighborsOf(
  nodeId: string,
  model: SchemaGraphModel,
): Set<string> {
  const out = new Set<string>();
  for (const b of model.bundles) {
    if (b.source === nodeId) out.add(b.target);
    if (b.target === nodeId) out.add(b.source);
  }
  return out;
}

function firstNumber(...vals: unknown[]): number | undefined {
  for (const v of vals) {
    if (typeof v === "number" && Number.isFinite(v)) return v;
  }
  return undefined;
}

/**
 * Best-effort extraction of per-relationship instance counts from the
 * free-form `/schema/statistics` `statistics` block. The analyzer's exact
 * shape is not pinned (`dict[str, Any]`), so we tolerate a flat
 * `{ <relType>: {count|cardinality|...} }` or a nested `{ relationships:
 * {...} }` and pull the first numeric count-like field. Missing/odd shapes
 * yield `{}` → the graph renders unweighted rather than erroring.
 */
export function extractRelationshipCounts(
  statistics: Record<string, unknown> | null | undefined,
): Record<string, number> {
  const out: Record<string, number> = {};
  if (!statistics || typeof statistics !== "object") return out;
  const nested = (statistics as Record<string, unknown>).relationships;
  const container =
    nested && typeof nested === "object"
      ? (nested as Record<string, unknown>)
      : (statistics as Record<string, unknown>);
  for (const [key, val] of Object.entries(container)) {
    if (!val || typeof val !== "object") continue;
    const v = val as Record<string, unknown>;
    const n = firstNumber(
      v.count,
      v.cardinality,
      v.instances,
      v.edgeCount,
      v.total,
      v.frequency,
    );
    if (n != null) {
      out[key] = n;
      out[localOf(key)] = n;
    }
  }
  return out;
}
