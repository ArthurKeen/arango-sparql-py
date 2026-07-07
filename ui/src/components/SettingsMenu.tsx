import { useEffect, useRef, useState } from "react";
import { themeModeLabel, type ThemeMode } from "../utils/theme";

// Gear/settings popover (Query Workbench Shell, L2 — PRD §10.16). Holds
// the workspace-panel triggers (Ontology, Outline, Samples, History) and
// behaviour preferences so the header stays free of configuration
// clutter. Mirrors `references/arango-cypher-py/ui/src/components/
// SettingsMenu.tsx`, trimmed for the SPARQL service: there is a single
// NL path (/nl-translate → SPARQL → AQL), so the Cypher UI's
// NL-output-mode and auto-translate/auto-run toggles are intentionally
// omitted (the Send pipeline in §10.0 covers translate+run in one step).

export interface SettingsMenuProps {
  showMapping: boolean;
  onToggleMapping: () => void;
  showOutline: boolean;
  onToggleOutline: () => void;
  showTranscript: boolean;
  onToggleTranscript: () => void;
  onOpenSamples: () => void;
  onOpenHistory: () => void;
  onOpenPalette: () => void;
  historyCount: number;
  autoOpenOnError: boolean;
  onToggleAutoOpenOnError: () => void;
  themeMode: ThemeMode;
  onCycleTheme: () => void;
}

// Show the Mod-K accelerator with the platform-correct modifier glyph.
const PALETTE_HINT =
  typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform)
    ? "⌘K"
    : "Ctrl K";

function GearIcon() {
  return (
    <svg
      className="w-4 h-4"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </svg>
  );
}

function ToggleRow({
  label,
  description,
  active,
  onClick,
}: {
  label: string;
  description?: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={active}
      onClick={onClick}
      className="w-full flex items-center justify-between gap-3 px-3 py-2 text-left hover:bg-gray-800/60 transition-colors"
    >
      <span className="min-w-0">
        <span className="block text-xs text-gray-200">{label}</span>
        {description && (
          <span className="block text-[10px] text-gray-500">{description}</span>
        )}
      </span>
      <span
        className={`relative inline-flex h-4 w-7 shrink-0 items-center rounded-full transition-colors ${
          active ? "bg-indigo-600" : "bg-gray-700"
        }`}
      >
        <span
          className={`inline-block h-3 w-3 transform rounded-full bg-white transition-transform ${
            active ? "translate-x-3.5" : "translate-x-0.5"
          }`}
        />
      </span>
    </button>
  );
}

function ActionRow({
  label,
  badge,
  onClick,
}: {
  label: string;
  badge?: string | number;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="w-full flex items-center justify-between gap-3 px-3 py-2 text-left hover:bg-gray-800/60 transition-colors"
    >
      <span className="text-xs text-gray-200">{label}</span>
      {badge !== undefined && badge !== "" && (
        <span className="text-[10px] text-gray-500 tabular-nums">{badge}</span>
      )}
    </button>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="px-3 pt-2.5 pb-1 text-[10px] font-semibold uppercase tracking-wider text-gray-600">
      {children}
    </div>
  );
}

export default function SettingsMenu(props: SettingsMenuProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onPointer = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const runItem = (fn: () => void) => () => {
    fn();
    setOpen(false);
  };

  return (
    <div className="relative" ref={rootRef}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Settings"
        title="Settings"
        className={`flex items-center justify-center w-8 h-8 rounded transition-colors ${
          open
            ? "bg-indigo-600/20 text-indigo-400 border border-indigo-600/30"
            : "bg-gray-800 text-gray-400 hover:text-gray-200"
        }`}
      >
        <GearIcon />
      </button>

      {open && (
        <div
          role="menu"
          className="absolute right-0 top-full mt-1 z-50 w-64 rounded-lg border border-gray-700 bg-gray-900 shadow-xl overflow-hidden"
        >
          <SectionLabel>Panels</SectionLabel>
          <ToggleRow
            label="Ontology"
            description="Turtle / OWL mapping panel"
            active={props.showMapping}
            onClick={props.onToggleMapping}
          />
          <ToggleRow
            label="Clause outline"
            description="Jump to SPARQL clauses"
            active={props.showOutline}
            onClick={props.onToggleOutline}
          />
          <ToggleRow
            label="Conversation"
            description="Multi-turn transcript of your questions"
            active={props.showTranscript}
            onClick={props.onToggleTranscript}
          />
          <ActionRow label="Sample queries" onClick={runItem(props.onOpenSamples)} />
          <ActionRow
            label="Query history"
            badge={props.historyCount > 0 ? props.historyCount : ""}
            onClick={runItem(props.onOpenHistory)}
          />
          <ActionRow
            label="Command palette"
            badge={PALETTE_HINT}
            onClick={runItem(props.onOpenPalette)}
          />

          <div className="my-1 border-t border-gray-800" />

          <SectionLabel>Behavior</SectionLabel>
          <ToggleRow
            label="Open inspector on error"
            description="Reveal the editors when a query fails"
            active={props.autoOpenOnError}
            onClick={props.onToggleAutoOpenOnError}
          />

          <div className="my-1 border-t border-gray-800" />

          <SectionLabel>Appearance</SectionLabel>
          {/* Cycles system → dark → light; kept open so users can flip freely. */}
          <ActionRow
            label="Theme"
            badge={themeModeLabel(props.themeMode)}
            onClick={props.onCycleTheme}
          />
        </div>
      )}
    </div>
  );
}
