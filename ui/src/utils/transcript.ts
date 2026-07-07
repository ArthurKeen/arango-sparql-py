// Multi-turn transcript model (WP-UI-SHELL Phase 4, PRD §10.0).
//
// The workbench is single-active-query by default; Phase 4 keeps a
// session-scoped, scrollable record of NL "Ask" turns (question → the
// SPARQL it produced) so users can scroll back and reload an earlier
// question's query. Pure so the append/cap contract is unit-testable.

export interface TranscriptTurn {
  id: string;
  question: string;
  /** The generated SPARQL, or null when generation failed. */
  sparql: string | null;
  ok: boolean;
  timestamp: number;
}

export const MAX_TRANSCRIPT = 50;

/** Append a turn, keeping only the most recent `cap` (immutable). */
export function appendTurn(
  list: TranscriptTurn[],
  turn: TranscriptTurn,
  cap = MAX_TRANSCRIPT,
): TranscriptTurn[] {
  return [...list, turn].slice(-cap);
}

export function turnStatusLabel(turn: TranscriptTurn): string {
  return turn.ok ? "SPARQL generated" : "Generation failed";
}
