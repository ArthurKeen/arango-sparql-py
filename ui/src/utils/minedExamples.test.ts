import { describe, expect, it } from "vitest";

import { examplesForGraph, mergeSuggestions, type MinedExample } from "./minedExamples";

function example(question: string, graph: string | null): MinedExample {
  return {
    question,
    sparql: "PREFIX : <http://ex.org/#>\nSELECT ?a ?b WHERE { ?a :KNOWS ?b }",
    aql: "FOR doc1 IN @@_c1 RETURN doc1",
    graph,
    source: { collection: "_queries", key: "1", name: "Who knows whom", description: "" },
    verification: { verdict: "same_vertices", verified_at: "2026-10-13T00:00:00+00:00" },
  };
}

describe("examplesForGraph", () => {
  const social = example("Who knows whom?", "social");
  const billing = example("Unpaid invoices?", "billing");
  const editor = example("Everyone's name?", null);

  it("keeps the selected graph's examples and the graph-less ones", () => {
    expect(examplesForGraph([social, billing, editor], "social")).toEqual([social, editor]);
  });

  it("keeps every example when no graph is selected", () => {
    expect(examplesForGraph([social, billing, editor], null)).toEqual([social, billing, editor]);
  });
});

describe("mergeSuggestions", () => {
  it("puts the user's history first, then mined examples, then schema samples", () => {
    expect(mergeSuggestions(["my question"], [example("Who knows whom?", null)], ["list all people"])).toEqual([
      "my question",
      "Who knows whom?",
      "list all people",
    ]);
  });

  it("drops duplicates case-insensitively and blanks, and caps the list", () => {
    const merged = mergeSuggestions(["Who knows whom?", "  "], [example("who knows WHOM?", null)], ["a", "b"], 2);
    expect(merged).toEqual(["Who knows whom?", "a"]);
  });
});
