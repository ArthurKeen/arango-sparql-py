// Pure logic for the command palette (Mod-K, PRD §10.7 / WP-UI-PALETTE).
// The palette is a keyboard-first index of every workbench action so
// power users don't have to hunt through the gear menu. Keeping match /
// filter / navigation logic pure makes it testable without a DOM harness;
// the React component (`CommandPalette.tsx`) only renders + wires keys.

export interface Command {
  /** Stable id (used as the React key and in tests). */
  id: string;
  /** Human label shown in the list. */
  title: string;
  /** Grouping header (e.g. "Query", "Panels", "View"). */
  section: string;
  /** Right-aligned hint, e.g. a shortcut or state ("on"/"off"). */
  hint?: string;
  /** Extra search terms that don't appear in the title. */
  keywords?: string;
  /** Disabled commands still render (greyed) but can't be selected/run. */
  enabled: boolean;
  /** Side-effecting handler — invoked when the command is chosen. */
  run: () => void;
}

/** Case-insensitive substring match over title + section + keywords. */
export function matchesQuery(cmd: Command, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  const hay = `${cmd.title} ${cmd.section} ${cmd.keywords ?? ""}`.toLowerCase();
  // AND across whitespace-separated terms so "run q" narrows sensibly.
  return q.split(/\s+/).every((term) => hay.includes(term));
}

/**
 * Filter the command list by the query. Order is preserved except that
 * title-prefix matches float to the top (so typing "exp" surfaces
 * "Explain" above a command that merely mentions "export"). Stable within
 * each bucket.
 */
export function filterCommands(cmds: Command[], query: string): Command[] {
  const matched = cmds.filter((c) => matchesQuery(c, query));
  const q = query.trim().toLowerCase();
  if (!q) return matched;
  const prefix: Command[] = [];
  const rest: Command[] = [];
  for (const c of matched) {
    if (c.title.toLowerCase().startsWith(q)) prefix.push(c);
    else rest.push(c);
  }
  return [...prefix, ...rest];
}

/**
 * Move the selection to the next *enabled* command in `dir` (+1 / -1),
 * skipping disabled rows and wrapping around. Returns the current index
 * unchanged when nothing is enabled. `list` is the already-filtered set.
 */
export function nextEnabledIndex(
  list: Command[],
  current: number,
  dir: 1 | -1,
): number {
  if (list.length === 0) return current;
  if (!list.some((c) => c.enabled)) return current;
  let i = current;
  for (let step = 0; step < list.length; step++) {
    i = (i + dir + list.length) % list.length;
    if (list[i]?.enabled) return i;
  }
  return current;
}

/**
 * The index of the first enabled command (the default selection when the
 * filtered list changes). Returns -1 when none are enabled.
 */
export function firstEnabledIndex(list: Command[]): number {
  return list.findIndex((c) => c.enabled);
}
