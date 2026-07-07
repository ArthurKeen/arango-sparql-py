import { describe, it, expect } from "vitest";
import type { OwlClass, OwlProperty } from "../api/client";
import {
  buildSchemaModel,
  bundleKey,
  bundleLabel,
  edgeWidth,
  matchSchema,
  neighborsOf,
  extractRelationshipCounts,
  MIN_EDGE_WIDTH,
  MAX_EDGE_WIDTH,
} from "./schemaGraph";

const P = "http://ex/";
const cls = (local: string): OwlClass => ({
  iri: P + local,
  localName: local,
  superClasses: [],
});
const objProp = (local: string, dom: string, rng: string): OwlProperty => ({
  iri: P + local,
  localName: local,
  domain: [P + dom],
  range: [P + rng],
  kind: "object",
});
const dataProp = (local: string, dom: string): OwlProperty => ({
  iri: P + local,
  localName: local,
  domain: [P + dom],
  range: [],
  kind: "datatype",
});

const CLASSES = [cls("Person"), cls("Company"), cls("City")];
const PROPS = [
  objProp("worksFor", "Person", "Company"),
  objProp("employedBy", "Person", "Company"), // same pair → bundled
  objProp("livesIn", "Person", "City"),
  dataProp("name", "Person"),
];

describe("buildSchemaModel", () => {
  it("bundles object properties sharing a (domain,range) pair", () => {
    const m = buildSchemaModel(CLASSES, PROPS);
    expect(m.nodes).toHaveLength(3);
    // worksFor + employedBy collapse into one Person→Company bundle.
    const pc = m.bundles.find(
      (b) => b.id === bundleKey(P + "Person", P + "Company"),
    );
    expect(pc?.members.map((x) => x.localName).sort()).toEqual([
      "employedBy",
      "worksFor",
    ]);
    // Person→City is its own bundle.
    expect(m.bundles).toHaveLength(2);
  });

  it("attaches datatype properties to their domain node, not as edges", () => {
    const m = buildSchemaModel(CLASSES, PROPS);
    const person = m.nodes.find((n) => n.label === "Person");
    expect(person?.datatypeProps).toContain("name");
    expect(m.bundles.every((b) => b.members.every((x) => x.localName !== "name"))).toBe(true);
  });

  it("skips edges whose endpoint has no class node", () => {
    const m = buildSchemaModel(
      [cls("Person")],
      [objProp("worksFor", "Person", "Company")], // Company node absent
    );
    expect(m.bundles).toHaveLength(0);
  });

  it("sums counts onto the bundle and tracks the max", () => {
    const m = buildSchemaModel(CLASSES, PROPS, { worksFor: 10, employedBy: 5, livesIn: 3 });
    const pc = m.bundles.find((b) => b.target === P + "Company");
    expect(pc?.count).toBe(15);
    expect(m.maxBundleCount).toBe(15);
  });
});

describe("edgeWidth", () => {
  it("returns the base width with no stats", () => {
    expect(edgeWidth(0, 0)).toBe(MIN_EDGE_WIDTH);
  });
  it("maps the busiest edge to the max width", () => {
    expect(edgeWidth(100, 100)).toBeCloseTo(MAX_EDGE_WIDTH, 5);
  });
  it("is monotonic between min and max", () => {
    const a = edgeWidth(5, 100);
    const b = edgeWidth(50, 100);
    expect(a).toBeGreaterThanOrEqual(MIN_EDGE_WIDTH);
    expect(b).toBeGreaterThan(a);
    expect(b).toBeLessThanOrEqual(MAX_EDGE_WIDTH);
  });
});

describe("bundleLabel", () => {
  it("shows a single predicate name plainly", () => {
    const m = buildSchemaModel([cls("Person"), cls("City")], [objProp("livesIn", "Person", "City")]);
    expect(bundleLabel(m.bundles[0])).toBe("livesIn");
  });
  it("shows +N for a bundled pair", () => {
    const m = buildSchemaModel(CLASSES, PROPS);
    const pc = m.bundles.find((b) => b.target === P + "Company")!;
    expect(bundleLabel(pc)).toMatch(/ \+1$/);
  });
});

describe("matchSchema", () => {
  const model = buildSchemaModel(CLASSES, PROPS);

  it("is inactive for an empty query", () => {
    expect(matchSchema("  ", model).active).toBe(false);
  });

  it("matches class labels and includes neighbours in focus", () => {
    const r = matchSchema("company", model);
    expect(r.matchedNodeIds.has(P + "Company")).toBe(true);
    // Person is a neighbour of Company, so it stays in focus.
    expect(r.focusNodeIds.has(P + "Person")).toBe(true);
    // City is not connected to Company → dimmed (out of focus).
    expect(r.focusNodeIds.has(P + "City")).toBe(false);
  });

  it("matches predicate names and lights up both endpoints", () => {
    const r = matchSchema("livesin", model);
    expect(r.matchedNodeIds.has(P + "Person")).toBe(true);
    expect(r.matchedNodeIds.has(P + "City")).toBe(true);
    expect(r.matchedBundleIds.has(bundleKey(P + "Person", P + "City"))).toBe(true);
  });
});

describe("neighborsOf", () => {
  it("returns nodes connected in either direction", () => {
    const model = buildSchemaModel(CLASSES, PROPS);
    const n = neighborsOf(P + "Person", model);
    expect(n.has(P + "Company")).toBe(true);
    expect(n.has(P + "City")).toBe(true);
  });
});

describe("extractRelationshipCounts", () => {
  it("reads a flat { relType: { count } } shape", () => {
    const c = extractRelationshipCounts({ worksFor: { count: 12 }, livesIn: { cardinality: 4 } });
    expect(c.worksFor).toBe(12);
    expect(c.livesIn).toBe(4);
  });
  it("reads a nested { relationships: {...} } shape", () => {
    const c = extractRelationshipCounts({ relationships: { knows: { instances: 7 } } });
    expect(c.knows).toBe(7);
  });
  it("also keys by the local name of an IRI-keyed stat", () => {
    const c = extractRelationshipCounts({ "http://ex/worksFor": { count: 9 } });
    expect(c["http://ex/worksFor"]).toBe(9);
    expect(c.worksFor).toBe(9);
  });
  it("tolerates junk", () => {
    expect(extractRelationshipCounts(null)).toEqual({});
    expect(extractRelationshipCounts({ a: 5, b: "x" })).toEqual({});
  });
});
