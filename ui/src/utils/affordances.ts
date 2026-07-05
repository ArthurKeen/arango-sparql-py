// Pure logic for the per-result affordance chips (Query Workbench Shell,
// PRD §10.0 / WP-UI-SHELL Phase 3). The shell surfaces power features as
// chips under each answer instead of a permanent toolbar. Keeping the
// enable/active derivation pure makes it testable without a DOM harness.
//
// SPARQL set: View SPARQL, View AQL, Graph. Explain / Profile chips land
// with WP-UI-EXPLAIN once the results panel renders plan trees.

import type { ResultTab } from "../api/store";

export type AffordanceId = "sparql" | "aql" | "graph";

export interface Affordance {
  id: AffordanceId;
  label: string;
  /** Whether the chip is actionable (the underlying artifact exists). */
  enabled: boolean;
  /** Whether the chip reflects the currently-shown surface. */
  active: boolean;
  title: string;
}

export interface AffordanceInputs {
  /** A result set is present (a query has run). */
  hasResults: boolean;
  /** The SPARQL editor has content to reveal. */
  sparqlPresent: boolean;
  /** A transpiled AQL preview exists to reveal. */
  aqlPresent: boolean;
  /** The inspector is open (so "view" chips read as active). */
  inspectorOpen: boolean;
  /** The active results tab (so the Graph chip reads as active). */
  activeTab: ResultTab;
}

export function resultAffordances(i: AffordanceInputs): Affordance[] {
  return [
    {
      id: "sparql",
      label: "View SPARQL",
      enabled: i.sparqlPresent,
      active: i.inspectorOpen,
      title: "Open the inspector focused on the SPARQL editor",
    },
    {
      id: "aql",
      label: "View AQL",
      enabled: i.aqlPresent,
      active: i.inspectorOpen,
      title: "Open the inspector focused on the transpiled AQL",
    },
    {
      id: "graph",
      label: "Graph",
      enabled: i.hasResults,
      active: i.activeTab === "graph",
      title: "Render the result bindings as a graph",
    },
  ];
}

/** True when at least one chip is actionable (used to hide the bar). */
export function anyAffordanceEnabled(list: Affordance[]): boolean {
  return list.some((a) => a.enabled);
}
