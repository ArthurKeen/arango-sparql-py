import { useEffect, useMemo, useRef, useState } from "react";
import {
  filterCommands,
  firstEnabledIndex,
  nextEnabledIndex,
  type Command,
} from "../utils/commandPalette";

// Command palette (Mod-K, PRD §10.7 / WP-UI-PALETTE). A keyboard-first
// overlay listing every workbench action. Selection logic lives in
// `utils/commandPalette.ts`; this component is the modal shell + keys.
// Per `ui-architecture.mdc` rule 19, every action here is also reachable
// from the gear menu — the palette accelerates, it doesn't replace.

export interface CommandPaletteProps {
  open: boolean;
  onClose: () => void;
  commands: Command[];
}

export default function CommandPalette({
  open,
  onClose,
  commands,
}: CommandPaletteProps) {
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const filtered = useMemo(
    () => filterCommands(commands, query),
    [commands, query],
  );

  // Reset query + focus the input each time the palette opens.
  useEffect(() => {
    if (open) {
      setQuery("");
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open]);

  // Keep the selection on the first enabled row whenever the filter set
  // changes (typing narrows the list).
  useEffect(() => {
    setActive(Math.max(0, firstEnabledIndex(filtered)));
  }, [filtered]);

  // Keep the active row scrolled into view.
  useEffect(() => {
    const el = listRef.current?.querySelector<HTMLElement>(
      `[data-idx="${active}"]`,
    );
    el?.scrollIntoView({ block: "nearest" });
  }, [active]);

  if (!open) return null;

  const runAt = (idx: number) => {
    const cmd = filtered[idx];
    if (!cmd || !cmd.enabled) return;
    onClose();
    cmd.run();
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => nextEnabledIndex(filtered, i, 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => nextEnabledIndex(filtered, i, -1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      runAt(active);
    } else if (e.key === "Escape") {
      e.preventDefault();
      onClose();
    }
  };

  let lastSection: string | null = null;

  return (
    <div
      className="fixed inset-0 z-[60] flex items-start justify-center pt-[12vh] bg-black/50"
      onMouseDown={onClose}
    >
      <div
        role="dialog"
        aria-label="Command palette"
        className="w-full max-w-lg mx-4 rounded-lg border border-gray-700 bg-gray-900 shadow-2xl overflow-hidden"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <input
          ref={inputRef}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Type a command…"
          className="w-full px-4 py-3 bg-transparent text-sm text-gray-100 placeholder-gray-500 border-b border-gray-800 outline-none"
          aria-label="Command search"
        />
        <div ref={listRef} className="max-h-80 overflow-y-auto py-1">
          {filtered.length === 0 ? (
            <div className="px-4 py-6 text-center text-xs text-gray-500">
              No matching commands
            </div>
          ) : (
            filtered.map((cmd, idx) => {
              const header =
                cmd.section !== lastSection ? cmd.section : null;
              lastSection = cmd.section;
              const isActive = idx === active;
              return (
                <div key={cmd.id}>
                  {header && (
                    <div className="px-4 pt-2 pb-1 text-[10px] font-semibold uppercase tracking-wider text-gray-600">
                      {header}
                    </div>
                  )}
                  <button
                    type="button"
                    data-idx={idx}
                    disabled={!cmd.enabled}
                    onMouseEnter={() => cmd.enabled && setActive(idx)}
                    onClick={() => runAt(idx)}
                    className={`w-full flex items-center justify-between gap-3 px-4 py-2 text-left transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${
                      isActive && cmd.enabled ? "bg-indigo-600/20" : ""
                    }`}
                  >
                    <span
                      className={`text-xs ${
                        isActive && cmd.enabled
                          ? "text-indigo-200"
                          : "text-gray-200"
                      }`}
                    >
                      {cmd.title}
                    </span>
                    {cmd.hint && (
                      <span className="text-[10px] text-gray-500 tabular-nums shrink-0">
                        {cmd.hint}
                      </span>
                    )}
                  </button>
                </div>
              );
            })
          )}
        </div>
      </div>
    </div>
  );
}
