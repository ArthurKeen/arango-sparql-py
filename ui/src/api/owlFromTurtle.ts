// Client-side OWL/Turtle → schema-graph projection.
//
// The server-side counterpart is `arango_sparql.translate.owl.owl_graph_view`;
// both produce the identical `{ classes, properties }` shape consumed by
// `CytoscapeSchemaGraph`, so the GRAPH tab renders an in-editor ontology the
// same way it renders a database-derived one. We parse in the browser (via
// `n3`) so the user sees the graph of whatever Turtle they are editing
// *immediately*, with no round-trip and even against an empty database.

import { Parser, type Quad } from "n3";
import type { OwlClass, OwlProperty } from "./client";

const RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type";
const RDFS_SUBCLASS_OF = "http://www.w3.org/2000/01/rdf-schema#subClassOf";
const RDFS_DOMAIN = "http://www.w3.org/2000/01/rdf-schema#domain";
const RDFS_RANGE = "http://www.w3.org/2000/01/rdf-schema#range";
const RDFS_COMMENT = "http://www.w3.org/2000/01/rdf-schema#comment";

const OWL_CLASS = "http://www.w3.org/2002/07/owl#Class";
const OWL_OBJECT_PROPERTY = "http://www.w3.org/2002/07/owl#ObjectProperty";
const OWL_DATATYPE_PROPERTY = "http://www.w3.org/2002/07/owl#DatatypeProperty";
const OWL_ANNOTATION_PROPERTY =
  "http://www.w3.org/2002/07/owl#AnnotationProperty";

// Property-kind precedence, mirroring the backend: a resource typed as more
// than one is emitted once, under the first matching kind.
const PROPERTY_KINDS: Array<[string, OwlProperty["kind"]]> = [
  [OWL_OBJECT_PROPERTY, "object"],
  [OWL_DATATYPE_PROPERTY, "datatype"],
  [OWL_ANNOTATION_PROPERTY, "annotation"],
];

export interface OwlSchemaView {
  classes: OwlClass[];
  properties: OwlProperty[];
}

const EMPTY_VIEW: OwlSchemaView = { classes: [], properties: [] };

/** Derive the local name of an IRI (the part after the last `#` or `/`). */
function localName(iri: string): string {
  const hash = iri.lastIndexOf("#");
  if (hash >= 0 && hash < iri.length - 1) return iri.slice(hash + 1);
  const slash = iri.lastIndexOf("/");
  if (slash >= 0 && slash < iri.length - 1) return iri.slice(slash + 1);
  return iri;
}

/**
 * Parse an OWL/Turtle ontology into the schema-graph shape.
 *
 * Empty / whitespace-only input returns empty lists (the GRAPH tab treats
 * "nothing to draw" as a normal state). Malformed Turtle throws — the caller
 * is expected to surface the message rather than render a partial graph.
 */
export function owlSchemaFromTurtle(turtle: string | null | undefined): OwlSchemaView {
  if (!turtle || !turtle.trim()) return EMPTY_VIEW;

  let quads: Quad[];
  try {
    // n3's `parse` is synchronous (returns the full quad array) when no
    // callback is supplied, and throws on a syntax error.
    quads = new Parser().parse(turtle);
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    throw new Error(`Failed to parse Turtle: ${msg}`);
  }

  // First pass: index the facts we need keyed by subject IRI.
  const types = new Map<string, Set<string>>();
  const subClassOf = new Map<string, Set<string>>();
  const domains = new Map<string, Set<string>>();
  const ranges = new Map<string, Set<string>>();
  const comments = new Map<string, string>();

  const addTo = (
    map: Map<string, Set<string>>,
    key: string,
    value: string,
  ): void => {
    const set = map.get(key) ?? new Set<string>();
    set.add(value);
    map.set(key, set);
  };

  for (const q of quads) {
    if (q.subject.termType !== "NamedNode") continue;
    const subj = q.subject.value;
    const pred = q.predicate.value;
    const obj = q.object;

    if (pred === RDF_TYPE && obj.termType === "NamedNode") {
      addTo(types, subj, obj.value);
    } else if (pred === RDFS_SUBCLASS_OF && obj.termType === "NamedNode") {
      addTo(subClassOf, subj, obj.value);
    } else if (pred === RDFS_DOMAIN && obj.termType === "NamedNode") {
      addTo(domains, subj, obj.value);
    } else if (pred === RDFS_RANGE && obj.termType === "NamedNode") {
      addTo(ranges, subj, obj.value);
    } else if (pred === RDFS_COMMENT && obj.termType === "Literal") {
      if (obj.value && !comments.has(subj)) comments.set(subj, obj.value);
    }
  }

  const sortedIris = (map: Map<string, Set<string>>, key: string): string[] =>
    Array.from(map.get(key) ?? []).sort();

  // Classes → nodes.
  const classes: OwlClass[] = [];
  const classIris = Array.from(types.entries())
    .filter(([, t]) => t.has(OWL_CLASS))
    .map(([iri]) => iri)
    .sort();
  for (const iri of classIris) {
    const name = localName(iri);
    if (!name) continue;
    const cls: OwlClass = {
      iri,
      localName: name,
      superClasses: sortedIris(subClassOf, iri),
    };
    const comment = comments.get(iri);
    if (comment) cls.comment = comment;
    classes.push(cls);
  }

  // Properties → edges / property bags.
  const properties: OwlProperty[] = [];
  const seen = new Set<string>();
  for (const [rdfType, kind] of PROPERTY_KINDS) {
    const propIris = Array.from(types.entries())
      .filter(([iri, t]) => t.has(rdfType) && !seen.has(iri))
      .map(([iri]) => iri)
      .sort();
    for (const iri of propIris) {
      const name = localName(iri);
      if (!name) continue;
      seen.add(iri);
      const prop: OwlProperty = {
        iri,
        localName: name,
        domain: sortedIris(domains, iri),
        range: sortedIris(ranges, iri),
        kind,
      };
      const comment = comments.get(iri);
      if (comment) prop.comment = comment;
      properties.push(prop);
    }
  }

  return { classes, properties };
}
