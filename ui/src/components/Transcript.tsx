import { useEffect, useRef } from "react";
import { turnStatusLabel, type TranscriptTurn } from "../utils/transcript";

// Multi-turn transcript (WP-UI-SHELL Phase 4, PRD §10.0). A compact,
// scrollable record of NL "Ask" turns above the composer; clicking a turn
// reloads its SPARQL into the editor. Opt-in via the gear menu so the
// single-active-query default is preserved for users who don't want it.

interface Props {
  turns: TranscriptTurn[];
  onSelect: (sparql: string) => void;
  onClear: () => void;
}

export default function Transcript({ turns, onSelect, onClear }: Props) {
  const endRef = useRef<HTMLDivElement>(null);

  // Keep the newest turn in view (chat convention: newest at the bottom).
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "nearest" });
  }, [turns.length]);

  return (
    <div className="flex flex-col border border-gray-800 rounded-md bg-gray-900/40 max-h-40">
      <div className="flex items-center justify-between px-3 py-1.5 border-b border-gray-800 shrink-0">
        <span className="text-[10px] font-semibold uppercase tracking-wider text-gray-500">
          Conversation
        </span>
        {turns.length > 0 && (
          <button
            onClick={onClear}
            className="text-[10px] text-gray-500 hover:text-red-300 transition-colors"
          >
            Clear
          </button>
        )}
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto px-2 py-1.5 space-y-1">
        {turns.length === 0 ? (
          <div className="px-1 py-2 text-[11px] text-gray-600">
            Ask a question below — each turn is recorded here so you can
            scroll back and reload an earlier query.
          </div>
        ) : (
          turns.map((turn) => (
            <button
              key={turn.id}
              type="button"
              disabled={!turn.sparql}
              onClick={() => turn.sparql && onSelect(turn.sparql)}
              title={turn.sparql ?? undefined}
              className="w-full text-left px-2 py-1.5 rounded hover:bg-gray-800/70 disabled:hover:bg-transparent disabled:cursor-default transition-colors group"
            >
              <div className="flex items-start gap-2">
                <span
                  className={`mt-0.5 shrink-0 inline-block w-1.5 h-1.5 rounded-full ${
                    turn.ok ? "bg-emerald-500" : "bg-red-500"
                  }`}
                  aria-hidden="true"
                />
                <div className="min-w-0 flex-1">
                  <div className="text-xs text-gray-300 truncate group-hover:text-gray-100">
                    {turn.question}
                  </div>
                  <div className="text-[10px] text-gray-600">
                    {turnStatusLabel(turn)}
                  </div>
                </div>
              </div>
            </button>
          ))
        )}
        <div ref={endRef} />
      </div>
    </div>
  );
}
