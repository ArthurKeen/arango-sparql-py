/**
 * Tests for the client-side OWL/Turtle → schema-graph parser.
 *
 * The shape is contractually identical to the backend's
 * `owl_graph_view` (see `tests/translate/test_owl.py`); these cases
 * mirror that suite so the two parsers cannot silently diverge.
 */
import { describe, expect, it } from "vitest";

import { owlSchemaFromTurtle } from "./owlFromTurtle";

const TTL = `
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix ex: <http://example.org/> .

ex:Person a owl:Class ; rdfs:comment "A person" .
ex:Org a owl:Class .
ex:Employee a owl:Class ; rdfs:subClassOf ex:Person .
ex:knows a owl:ObjectProperty ; rdfs:domain ex:Person ; rdfs:range ex:Person .
ex:worksAt a owl:ObjectProperty ; rdfs:domain ex:Person ; rdfs:range ex:Org .
ex:name a owl:DatatypeProperty ; rdfs:domain ex:Person ; rdfs:range rdfs:Literal .
ex:note a owl:AnnotationProperty ; rdfs:domain ex:Org .
`;

describe("owlSchemaFromTurtle", () => {
  it("projects classes with local names, super-classes, and comments", () => {
    const { classes } = owlSchemaFromTurtle(TTL);
    const byName = Object.fromEntries(classes.map((c) => [c.localName, c]));
    expect(Object.keys(byName).sort()).toEqual(["Employee", "Org", "Person"]);
    expect(byName.Person.comment).toBe("A person");
    expect(byName.Employee.superClasses).toEqual(["http://example.org/Person"]);
    expect(byName.Org.comment).toBeUndefined();
  });

  it("classifies property kinds (object / datatype / annotation)", () => {
    const { properties } = owlSchemaFromTurtle(TTL);
    const byName = Object.fromEntries(properties.map((p) => [p.localName, p]));
    expect(byName.knows.kind).toBe("object");
    expect(byName.worksAt.kind).toBe("object");
    expect(byName.name.kind).toBe("datatype");
    expect(byName.note.kind).toBe("annotation");
  });

  it("captures object-property domain/range as class IRIs", () => {
    const { properties } = owlSchemaFromTurtle(TTL);
    const works = properties.find((p) => p.localName === "worksAt")!;
    expect(works.domain).toEqual(["http://example.org/Person"]);
    expect(works.range).toEqual(["http://example.org/Org"]);
  });

  it.each([undefined, null, "", "   ", "\n\t"])(
    "returns empty lists for empty input %p",
    (input) => {
      expect(owlSchemaFromTurtle(input as string | null | undefined)).toEqual({
        classes: [],
        properties: [],
      });
    },
  );

  it("throws on malformed Turtle", () => {
    expect(() => owlSchemaFromTurtle("this is not turtle :{(")).toThrow(
      /parse Turtle/i,
    );
  });

  it("emits a property typed twice exactly once, object kind winning", () => {
    const ttl = `
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix ex: <http://example.org/> .
ex:rel a owl:ObjectProperty, owl:DatatypeProperty .
`;
    const rel = owlSchemaFromTurtle(ttl).properties.filter(
      (p) => p.localName === "rel",
    );
    expect(rel).toHaveLength(1);
    expect(rel[0].kind).toBe("object");
  });

  it("matches the Person/knows/name demo ontology shape", () => {
    const demo = `
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix ex: <http://example.org/> .

ex:Person a owl:Class ; rdfs:label "Person" .
ex:knows a owl:ObjectProperty ; rdfs:domain ex:Person ; rdfs:range ex:Person ; rdfs:label "knows" .
ex:name a owl:DatatypeProperty ; rdfs:domain ex:Person ; rdfs:range rdfs:Literal ; rdfs:label "name" .
`;
    const { classes, properties } = owlSchemaFromTurtle(demo);
    expect(classes.map((c) => c.localName)).toEqual(["Person"]);
    const knows = properties.find((p) => p.localName === "knows")!;
    expect(knows.kind).toBe("object");
    expect(knows.domain).toEqual(["http://example.org/Person"]);
    expect(knows.range).toEqual(["http://example.org/Person"]);
  });
});
