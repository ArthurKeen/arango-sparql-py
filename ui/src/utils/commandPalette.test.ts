/**
 * Tests for the command-palette pure logic (WP-UI-PALETTE).
 *
 * Cover the match/filter ranking and the enabled-aware keyboard
 * navigation so the DOM component stays a thin shell.
 */
import { describe, expect, it, vi } from "vitest";

import {
  filterCommands,
  firstEnabledIndex,
  matchesQuery,
  nextEnabledIndex,
  type Command,
} from "./commandPalette";

const cmd = (over: Partial<Command> = {}): Command => ({
  id: over.id ?? "id",
  title: over.title ?? "Title",
  section: over.section ?? "Query",
  enabled: over.enabled ?? true,
  run: over.run ?? vi.fn(),
  keywords: over.keywords,
  hint: over.hint,
});

const LIST: Command[] = [
  cmd({ id: "translate", title: "Translate to AQL", keywords: "transpile" }),
  cmd({ id: "run", title: "Run query", keywords: "execute", enabled: false }),
  cmd({ id: "explain", title: "Explain query" }),
  cmd({ id: "export", title: "Export ontology", section: "Schema" }),
];

describe("matchesQuery", () => {
  it("matches everything on an empty query", () => {
    expect(matchesQuery(cmd(), "")).toBe(true);
    expect(matchesQuery(cmd(), "   ")).toBe(true);
  });

  it("matches on title, section, and keywords case-insensitively", () => {
    expect(matchesQuery(cmd({ title: "Run query" }), "RUN")).toBe(true);
    expect(matchesQuery(cmd({ section: "Panels" }), "panel")).toBe(true);
    expect(matchesQuery(cmd({ keywords: "transpile" }), "transpile")).toBe(true);
  });

  it("ANDs whitespace-separated terms", () => {
    const c = cmd({ title: "Run query", keywords: "execute" });
    expect(matchesQuery(c, "run execute")).toBe(true);
    expect(matchesQuery(c, "run missing")).toBe(false);
  });
});

describe("filterCommands", () => {
  it("returns the whole list for an empty query", () => {
    expect(filterCommands(LIST, "")).toHaveLength(4);
  });

  it("floats title-prefix matches above mere substring matches", () => {
    // "exp" is a title prefix of "Explain query" but only a substring of
    // "Export ontology" (also a title prefix) — both prefix, order kept.
    const out = filterCommands(LIST, "exp");
    expect(out.map((c) => c.id)).toEqual(["explain", "export"]);
  });

  it("ranks a prefix match ahead of a keyword-only match", () => {
    const list = [
      cmd({ id: "a", title: "Zebra", keywords: "run" }),
      cmd({ id: "b", title: "Run query" }),
    ];
    expect(filterCommands(list, "run").map((c) => c.id)).toEqual(["b", "a"]);
  });

  it("excludes non-matches", () => {
    expect(filterCommands(LIST, "zzz")).toHaveLength(0);
  });
});

describe("nextEnabledIndex", () => {
  it("skips disabled rows and wraps", () => {
    // index 1 ("run") is disabled.
    expect(nextEnabledIndex(LIST, 0, 1)).toBe(2); // translate -> explain
    expect(nextEnabledIndex(LIST, 2, 1)).toBe(3); // explain -> export
    expect(nextEnabledIndex(LIST, 3, 1)).toBe(0); // export -> translate (wrap)
    expect(nextEnabledIndex(LIST, 0, -1)).toBe(3); // translate -> export (wrap back)
  });

  it("returns the current index when nothing is enabled", () => {
    const allOff = LIST.map((c) => cmd({ ...c, enabled: false }));
    expect(nextEnabledIndex(allOff, 0, 1)).toBe(0);
  });

  it("returns current for an empty list", () => {
    expect(nextEnabledIndex([], 0, 1)).toBe(0);
  });
});

describe("firstEnabledIndex", () => {
  it("finds the first enabled command", () => {
    const list = [cmd({ enabled: false }), cmd({ id: "x", enabled: true })];
    expect(firstEnabledIndex(list)).toBe(1);
  });

  it("returns -1 when none are enabled", () => {
    expect(firstEnabledIndex([cmd({ enabled: false })])).toBe(-1);
  });
});
