import { useMemo, useRef, useState, useEffect, useCallback } from "react";
import {
  parsePrefixes,
  resolvableMissingDeclarations,
  prefixLine,
  WELL_KNOWN_PREFIXES,
} from "../lang/sparqlComplete";

interface Props {
  value: string;
  onChange: (next: string) => void;
}

// WP-UI-EDITOR: compact prefix-management popover for the SPARQL pane.
// Surfaces used-but-undeclared prefixes and lets the user insert the
// resolvable ones (well-known IRIs) with one click, plus browse/add any
// well-known prefix that isn't declared yet. Declarations are prepended so
// they always precede the query body.
export default function PrefixManager({ value, onChange }: Props) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  const declared = useMemo(() => parsePrefixes(value), [value]);
  const { resolvable, unknown } = useMemo(
    () => resolvableMissingDeclarations(value),
    [value],
  );

  const addable = useMemo(
    () => Object.keys(WELL_KNOWN_PREFIXES).filter((p) => !(p in declared)).sort(),
    [declared],
  );

  const prepend = useCallback(
    (lines: string[]) => {
      if (lines.length === 0) return;
      const block = lines.join("\n");
      const sep = value.length && !value.startsWith("\n") ? "\n" : "";
      onChange(`${block}\n${sep}${value}`);
    },
    [value, onChange],
  );

  const addAllMissing = useCallback(() => {
    prepend(resolvable.map((r) => prefixLine(r.prefix, r.iri)));
    setOpen(false);
  }, [resolvable, prepend]);

  const addOne = useCallback(
    (prefix: string) => {
      const iri = WELL_KNOWN_PREFIXES[prefix];
      if (iri) prepend([prefixLine(prefix, iri)]);
    },
    [prepend],
  );

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const missingCount = resolvable.length + unknown.length;

  return (
    <div ref={rootRef} className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1 text-[10px] px-2 py-0.5 rounded border border-gray-700 text-gray-400 hover:text-gray-200 hover:border-gray-600"
        title="Manage SPARQL prefixes"
      >
        <span>Prefixes</span>
        {missingCount > 0 && (
          <span className="inline-flex items-center justify-center min-w-[14px] h-[14px] px-1 rounded-full bg-amber-500/20 text-amber-400 text-[9px] font-semibold">
            {missingCount}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 top-full mt-1 z-40 w-72 bg-gray-900 border border-gray-700 rounded-lg shadow-2xl text-xs">
          {resolvable.length > 0 && (
            <div className="p-2 border-b border-gray-800">
              <div className="flex items-center justify-between mb-1.5">
                <span className="text-amber-400 font-medium">
                  Missing ({resolvable.length})
                </span>
                <button
                  onClick={addAllMissing}
                  className="text-[10px] px-2 py-0.5 rounded bg-indigo-600 hover:bg-indigo-500 text-white"
                >
                  Add all
                </button>
              </div>
              <ul className="space-y-0.5">
                {resolvable.map((r) => (
                  <li key={r.prefix} className="flex items-center justify-between gap-2">
                    <code className="text-gray-300">{r.prefix}:</code>
                    <span className="text-gray-500 truncate flex-1 text-right">
                      {r.iri}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {unknown.length > 0 && (
            <div className="p-2 border-b border-gray-800">
              <span className="text-gray-500">
                Undeclared, unknown IRI: {unknown.map((u) => `${u}:`).join(", ")}
              </span>
            </div>
          )}

          <div className="p-2 max-h-56 overflow-y-auto">
            <div className="text-gray-500 mb-1.5">Add well-known prefix</div>
            {addable.length === 0 ? (
              <div className="text-gray-600">All well-known prefixes declared.</div>
            ) : (
              <ul className="space-y-0.5">
                {addable.map((p) => (
                  <li key={p}>
                    <button
                      onClick={() => addOne(p)}
                      className="w-full flex items-center justify-between gap-2 px-1 py-0.5 rounded hover:bg-gray-800 text-left"
                    >
                      <code className="text-gray-300">{p}:</code>
                      <span className="text-gray-600 truncate flex-1 text-right">
                        {WELL_KNOWN_PREFIXES[p]}
                      </span>
                      <span className="text-indigo-400">+</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
