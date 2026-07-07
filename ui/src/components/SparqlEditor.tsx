import { useEffect, useRef, useCallback } from "react";
import { EditorState, StateEffect, StateField, type Extension } from "@codemirror/state";
import {
  EditorView,
  Decoration,
  keymap,
  lineNumbers,
  highlightActiveLine,
  highlightActiveLineGutter,
  hoverTooltip,
} from "@codemirror/view";
import { defaultKeymap, history, historyKeymap } from "@codemirror/commands";
import { bracketMatching, foldGutter, foldKeymap } from "@codemirror/language";
import {
  autocompletion,
  completionKeymap,
  closeBrackets,
  closeBracketsKeymap,
} from "@codemirror/autocomplete";
import { searchKeymap, highlightSelectionMatches } from "@codemirror/search";
import { oneDark } from "./theme";
import { sparql } from "../lang/sparql";
import { sparqlCompletionSource, sparqlHoverInfo } from "../lang/sparqlComplete";

// Mirror of `references/arango-cypher-py/ui/src/components/CypherEditor.tsx`,
// re-pointed at the legacy SPARQL CodeMirror mode (see `lang/sparql.ts`).
// WP-UI-EDITOR: schema/prefix-aware completion, CURIE hover tooltips, and
// explain/profile keymaps ride on top of the syntax highlighting + the
// SPARQL ↔ AQL line-correspondence highlighter.

// Hover tooltip that expands the CURIE under the cursor to its full IRI.
const sparqlCurieHover = hoverTooltip((view, pos) => {
  const info = sparqlHoverInfo(view.state.doc.toString(), pos);
  if (!info) return null;
  return {
    pos: info.from,
    end: info.to,
    above: true,
    create() {
      const dom = document.createElement("div");
      dom.className = "cm-curie-tooltip";
      dom.textContent = info.iri;
      return { dom };
    },
  };
});

const setHighlightEffect = StateEffect.define<number[]>();

const highlightLineMark = Decoration.line({
  class: "cm-correspondence-highlight",
});

const highlightField: Extension = StateField.define({
  create() {
    return Decoration.none;
  },
  update(decos, tr) {
    for (const e of tr.effects) {
      if (e.is(setHighlightEffect)) {
        const lines = e.value;
        const builder: ReturnType<typeof Decoration.set> = Decoration.set(
          lines
            .filter((l) => l >= 1 && l <= tr.state.doc.lines)
            .sort((a, b) => a - b)
            .map((l) => highlightLineMark.range(tr.state.doc.line(l).from)),
        );
        return builder;
      }
    }
    return decos;
  },
  provide: (f) => EditorView.decorations.from(f),
});

interface Props {
  value: string;
  onChange: (value: string) => void;
  onTranslate: () => void;
  onExecute: () => void;
  onExplain?: () => void;
  onProfile?: () => void;
  viewRef?: React.MutableRefObject<EditorView | null>;
  highlightLines?: number[];
  onHoverLine?: (line: number | null) => void;
}

export default function SparqlEditor({
  value,
  onChange,
  onTranslate,
  onExecute,
  onExplain,
  onProfile,
  viewRef: externalViewRef,
  highlightLines,
  onHoverLine,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView | null>(null);
  const callbacksRef = useRef({ onChange, onTranslate, onExecute, onExplain, onProfile });
  callbacksRef.current = { onChange, onTranslate, onExecute, onExplain, onProfile };

  const onHoverLineRef = useRef(onHoverLine);
  onHoverLineRef.current = onHoverLine;

  const handleMouseMove = useCallback((e: MouseEvent) => {
    const view = viewRef.current;
    if (!view || !onHoverLineRef.current) return;
    const pos = view.posAtCoords({ x: e.clientX, y: e.clientY });
    if (pos != null) {
      const line = view.state.doc.lineAt(pos).number;
      onHoverLineRef.current(line);
    }
  }, []);

  const handleMouseLeave = useCallback(() => {
    onHoverLineRef.current?.(null);
  }, []);

  useEffect(() => {
    if (!containerRef.current) return;

    const workbenchKeymap = keymap.of([
      {
        key: "Mod-Enter",
        run: () => { callbacksRef.current.onTranslate(); return true; },
      },
      {
        key: "Shift-Enter",
        run: () => { callbacksRef.current.onExecute(); return true; },
      },
      {
        key: "Mod-Shift-e",
        run: () => { callbacksRef.current.onExplain?.(); return true; },
      },
      {
        key: "Mod-Shift-p",
        run: () => { callbacksRef.current.onProfile?.(); return true; },
      },
    ]);

    const state = EditorState.create({
      doc: value,
      extensions: [
        workbenchKeymap,
        lineNumbers(),
        highlightActiveLineGutter(),
        highlightActiveLine(),
        history(),
        foldGutter(),
        bracketMatching(),
        closeBrackets(),
        highlightSelectionMatches({ highlightWordAroundCursor: true, minSelectionLength: 1 }),
        sparql(),
        autocompletion({ override: [sparqlCompletionSource] }),
        sparqlCurieHover,
        oneDark,
        keymap.of([
          ...closeBracketsKeymap,
          ...completionKeymap,
          ...defaultKeymap,
          ...searchKeymap,
          ...historyKeymap,
          ...foldKeymap,
        ]),
        EditorView.updateListener.of((update) => {
          if (update.docChanged) {
            callbacksRef.current.onChange(update.state.doc.toString());
          }
        }),
        highlightField,
        EditorView.theme({
          "&": { height: "100%" },
          ".cm-scroller": { overflow: "auto" },
          ".cm-correspondence-highlight": {
            backgroundColor: "rgba(59, 130, 246, 0.1)",
          },
          ".cm-curie-tooltip": {
            padding: "2px 8px",
            fontFamily: "monospace",
            fontSize: "11px",
            color: "#93c5fd",
            backgroundColor: "#0b1220",
            border: "1px solid #1e293b",
            borderRadius: "4px",
            maxWidth: "480px",
            wordBreak: "break-all",
          },
        }),
      ],
    });

    const view = new EditorView({ state, parent: containerRef.current });
    viewRef.current = view;
    if (externalViewRef) externalViewRef.current = view;

    view.dom.addEventListener("mousemove", handleMouseMove);
    view.dom.addEventListener("mouseleave", handleMouseLeave);

    return () => {
      view.dom.removeEventListener("mousemove", handleMouseMove);
      view.dom.removeEventListener("mouseleave", handleMouseLeave);
      view.destroy();
      viewRef.current = null;
      if (externalViewRef) externalViewRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Sync external value changes (e.g. loading from history)
  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    const current = view.state.doc.toString();
    if (current !== value) {
      view.dispatch({
        changes: { from: 0, to: current.length, insert: value },
      });
    }
  }, [value]);

  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    view.dispatch({ effects: setHighlightEffect.of(highlightLines ?? []) });
  }, [highlightLines]);

  return <div ref={containerRef} className="h-full" />;
}
