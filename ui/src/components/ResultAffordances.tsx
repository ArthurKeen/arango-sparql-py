import type { Affordance, AffordanceId } from "../utils/affordances";
import { anyAffordanceEnabled } from "../utils/affordances";

// Per-result affordance chip bar (Query Workbench Shell, L0 — PRD §10.0 /
// WP-UI-SHELL Phase 3). Sits under the answer and lets the user jump to a
// power surface (SPARQL editor, AQL preview, graph view) without a
// permanent toolbar. Renders nothing when no chip is actionable.

export interface ResultAffordancesProps {
  affordances: Affordance[];
  onSelect: (id: AffordanceId) => void;
}

export default function ResultAffordances({
  affordances,
  onSelect,
}: ResultAffordancesProps) {
  if (!anyAffordanceEnabled(affordances)) return null;

  return (
    <div
      className="flex items-center gap-1.5 px-3 py-1 bg-gray-900/40 border-b border-gray-800"
      role="toolbar"
      aria-label="Result actions"
    >
      <span className="text-[10px] uppercase tracking-wider text-gray-600 mr-0.5">
        View
      </span>
      {affordances.map((a) => (
        <button
          key={a.id}
          type="button"
          onClick={() => onSelect(a.id)}
          disabled={!a.enabled}
          aria-pressed={a.active}
          title={a.title}
          className={`px-2 py-0.5 text-[10px] rounded border transition-colors disabled:opacity-30 disabled:cursor-not-allowed ${
            a.active && a.enabled
              ? "bg-indigo-600/20 text-indigo-300 border-indigo-600/30"
              : "bg-gray-800 text-gray-400 border-transparent hover:text-gray-200"
          }`}
        >
          {a.label}
        </button>
      ))}
    </div>
  );
}
