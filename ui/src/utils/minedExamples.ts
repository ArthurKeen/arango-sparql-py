// Verified examples mined from the connected database's own saved AQL queries
// (`arango-sparql-py mine-examples`, PRD §7.7): each question + SPARQL was
// checked to return the same resources as the saved query it came from.
// Ported from arango-cypher-py's ui/src/api/client.ts (MinedExample,
// examplesForGraph) and App.tsx (the Ask-suggestion merge).

export interface MinedExample {
  question: string;
  sparql: string;
  aql: string;
  graph: string | null;
  source: { collection: string; key: string; name: string; description: string };
  verification: { verdict: string; verified_at: string; model?: string };
}

// Sample Queries heading for mined examples; listed ahead of the built-ins.
export const MINED_CATEGORY = "From this database";

// Mined examples that belong to the selected named graph (and those tied to
// no graph, such as query-editor saves); every example when no graph is
// selected.
export function examplesForGraph(examples: MinedExample[], graph: string | null): MinedExample[] {
  if (!graph) return examples;
  return examples.filter((e) => !e.graph || e.graph === graph);
}

// Ask-bar suggestions: the user's own recent questions, then the database's
// verified examples, then the schema-derived samples — de-duplicated
// case-insensitively and capped.
export function mergeSuggestions(
  history: string[],
  examples: MinedExample[],
  samples: string[],
  limit: number = 10,
): string[] {
  const seen = new Set<string>();
  const merged: string[] = [];
  for (const text of [...history, ...examples.map((e) => e.question), ...samples]) {
    const key = text.trim().toLowerCase();
    if (key && !seen.has(key)) {
      seen.add(key);
      merged.push(text);
    }
  }
  return merged.slice(0, limit);
}
