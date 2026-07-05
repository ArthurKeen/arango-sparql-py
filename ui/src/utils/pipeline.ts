// Pure helpers for the chat composer's "Send" pipeline (Query Workbench
// Shell, PRD §10.0). Send always generates the source query and
// transpiles it; it executes only when connected. Keeping this logic
// pure makes the degradation contract testable without a DOM/React
// harness. Mirrors `references/arango-cypher-py/ui/src/utils/pipeline.ts`
// with SPARQL-flavoured stage labels (source = SPARQL, target = AQL).

export interface SendIntent {
  /** Run NL -> SPARQL -> AQL after generation. Always true for Send. */
  translate: boolean;
  /** Execute after a successful transpile. Only when connected. */
  run: boolean;
}

/**
 * Decide what a Send should do given connection state.
 *
 * - Connected: generate -> transpile -> execute.
 * - Disconnected: generate -> transpile only (results need a live DB).
 */
export function planSend(connected: boolean): SendIntent {
  return { translate: true, run: connected };
}

export const IDLE_INTENT: SendIntent = { translate: false, run: false };

export interface PipelineFlags {
  /** A /nl-translate call is in flight (store `generating`). */
  nlLoading: boolean;
  /** A /translate call is in flight (store `translating`). */
  translating: boolean;
  /** A /execute call is in flight (store `executing`). */
  executing: boolean;
}

export type PipelineStageId = "idle" | "generating" | "transpiling" | "running";

/**
 * The single in-progress stage, derived from the app's busy flags.
 * Returns ``"idle"`` when nothing is running. Generation is checked first
 * because the stages run in order (generate -> transpile -> run).
 */
export function currentStage(flags: PipelineFlags): PipelineStageId {
  if (flags.nlLoading) return "generating";
  if (flags.translating) return "transpiling";
  if (flags.executing) return "running";
  return "idle";
}

const STAGE_LABELS: Record<PipelineStageId, string> = {
  idle: "",
  generating: "Generating SPARQL\u2026",
  transpiling: "Transpiling to AQL\u2026",
  running: "Running\u2026",
};

export function stageLabel(stage: PipelineStageId): string {
  return STAGE_LABELS[stage];
}

export function isBusy(flags: PipelineFlags): boolean {
  return flags.nlLoading || flags.translating || flags.executing;
}
