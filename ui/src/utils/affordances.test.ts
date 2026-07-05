/**
 * Tests for the per-result affordance-chip logic (WP-UI-SHELL Phase 3).
 *
 * Pin which chips are enabled/active for a given app state. Pure
 * function, so no DOM harness needed.
 */
import { describe, expect, it } from "vitest";

import {
  anyAffordanceEnabled,
  resultAffordances,
  type AffordanceInputs,
} from "./affordances";

const INPUTS = (over: Partial<AffordanceInputs> = {}): AffordanceInputs => ({
  hasResults: false,
  sparqlPresent: false,
  aqlPresent: false,
  inspectorOpen: false,
  activeTab: "table",
  ...over,
});

const byId = (list: ReturnType<typeof resultAffordances>, id: string) =>
  list.find((a) => a.id === id)!;

describe("resultAffordances", () => {
  it("always returns the SPARQL/AQL/Graph triad", () => {
    const list = resultAffordances(INPUTS());
    expect(list.map((a) => a.id)).toEqual(["sparql", "aql", "graph"]);
  });

  it("disables every chip when nothing is available", () => {
    const list = resultAffordances(INPUTS());
    expect(anyAffordanceEnabled(list)).toBe(false);
  });

  it("enables the SPARQL chip only when SPARQL is present", () => {
    expect(byId(resultAffordances(INPUTS()), "sparql").enabled).toBe(false);
    expect(
      byId(resultAffordances(INPUTS({ sparqlPresent: true })), "sparql").enabled,
    ).toBe(true);
  });

  it("enables the AQL chip only when AQL is present", () => {
    expect(
      byId(resultAffordances(INPUTS({ aqlPresent: true })), "aql").enabled,
    ).toBe(true);
  });

  it("enables the Graph chip only when results exist", () => {
    expect(byId(resultAffordances(INPUTS()), "graph").enabled).toBe(false);
    expect(
      byId(resultAffordances(INPUTS({ hasResults: true })), "graph").enabled,
    ).toBe(true);
  });

  it("marks view chips active when the inspector is open", () => {
    const open = resultAffordances(INPUTS({ inspectorOpen: true }));
    expect(byId(open, "sparql").active).toBe(true);
    expect(byId(open, "aql").active).toBe(true);
  });

  it("marks the Graph chip active only on the graph tab", () => {
    expect(byId(resultAffordances(INPUTS()), "graph").active).toBe(false);
    expect(
      byId(resultAffordances(INPUTS({ activeTab: "graph" })), "graph").active,
    ).toBe(true);
  });
});
