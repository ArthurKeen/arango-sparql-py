import { useEffect, useRef, useCallback } from "react";
import { EditorState, StateEffect, StateField, type Extension } from "@codemirror/state";
import {
  EditorView,
  Decoration,
  keymap,
  lineNumbers,
  highlightActiveLine,
  highlightActiveLineGutter,
} from "@codemirror/view";
import { defaultKeymap, history, historyKeymap } from "@codemirror/commands";
import { bracketMatching, foldGutter, foldKeymap } from "@codemirror/language";
import { closeBrackets, closeBracketsKeymap } from "@codemirror/autocomplete";
import { searchKeymap, highlightSelectionMatches } from "@codemirror/search";
import { oneDark } from "./theme";
import { sparql } from "../lang/sparql";

// Mirror of `references/arango-cypher-py/ui/src/components/CypherEditor.tsx`,
// re-pointed at the legacy SPARQL CodeMirror mode (see `lang/sparql.ts`).
// SPARQL completion + hover land in a follow-up task — until then this
// editor only carries syntax highlighting + the SPARQL ↔ AQL line
// correspondence highlighter.

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
  viewRef?: React.MutableRefObject<EditorView | null>;
  highlightLines?: number[];
  onHoverLine?: (line: number | null) => void;
}

export default function SparqlEditor({
  value,
  onChange,
  onTranslate,
  onExecute,
  viewRef: externalViewRef,
  highlightLines,
  onHoverLine,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView | null>(null);
  const callbacksRef = useRef({ onChange, onTranslate, onExecute });
  callbacksRef.current = { onChange, onTranslate, onExecute };

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
        oneDark,
        keymap.of([
          ...closeBracketsKeymap,
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
