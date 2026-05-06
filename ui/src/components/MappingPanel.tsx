import { useCallback, useEffect, useRef, useState } from "react";
import { EditorState } from "@codemirror/state";
import { EditorView, lineNumbers, keymap } from "@codemirror/view";
import { defaultKeymap, history, historyKeymap } from "@codemirror/commands";
import { bracketMatching } from "@codemirror/language";
import { closeBrackets, closeBracketsKeymap } from "@codemirror/autocomplete";
import { oneDark } from "./theme";
import SchemaGraph from "./SchemaGraph";

// "Mapping" panel for the SPARQL UI. Where the Cypher project edits a
// JSON schema-mapping object, the SPARQL service expects an OWL/Turtle
// ontology body sent as `ontology_ttl` on /translate. The component
// retains the same name + slot in the layout so a developer who reads
// the Cypher UI can find it instantly — the contents are now a plain
// CodeMirror Turtle text editor with a "Graph" toggle that defers to
// `SchemaGraph` (placeholder until `/schema/owl` lands).

interface Props {
  ontologyTtl: string;
  onChange: (ttl: string) => void;
  onClose?: () => void;
}

const SAMPLE_TTL = `@prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix ex:   <http://example.org/> .

ex:Person a owl:Class ;
    rdfs:label "Person" .

ex:knows a owl:ObjectProperty ;
    rdfs:domain ex:Person ;
    rdfs:range  ex:Person ;
    rdfs:label  "knows" .

ex:name a owl:DatatypeProperty ;
    rdfs:domain ex:Person ;
    rdfs:range  rdfs:Literal ;
    rdfs:label  "name" .
`;

export default function MappingPanel({ ontologyTtl, onChange, onClose }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView | null>(null);
  const [viewMode, setViewMode] = useState<"text" | "graph">("text");
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  // Counter-based guard mirrors the Cypher MappingPanel: incremented
  // before every programmatic dispatch so the asynchronous update
  // listener can tell user edits apart from external sets.
  const externalUpdateCount = useRef(0);

  const initial = ontologyTtl && ontologyTtl.trim().length > 0 ? ontologyTtl : SAMPLE_TTL;

  useEffect(() => {
    if (!containerRef.current) return;

    const state = EditorState.create({
      doc: initial,
      extensions: [
        lineNumbers(),
        history(),
        bracketMatching(),
        closeBrackets(),
        oneDark,
        keymap.of([
          ...closeBracketsKeymap,
          ...defaultKeymap,
          ...historyKeymap,
        ]),
        EditorView.updateListener.of((update) => {
          if (!update.docChanged) return;
          if (externalUpdateCount.current > 0) {
            externalUpdateCount.current -= 1;
            return;
          }
          onChangeRef.current(update.state.doc.toString());
        }),
        EditorView.theme({
          "&": { height: "100%" },
          ".cm-scroller": { overflow: "auto" },
        }),
      ],
    });

    const view = new EditorView({ state, parent: containerRef.current });
    viewRef.current = view;

    if (!ontologyTtl || ontologyTtl.trim().length === 0) {
      onChangeRef.current(initial);
    }

    return () => {
      view.destroy();
      viewRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Sync CodeMirror when the ontologyTtl prop changes externally
  // (e.g. loaded from /schema/owl, sample-import, file upload).
  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    const currentText = view.state.doc.toString();
    if (ontologyTtl !== currentText && ontologyTtl !== "") {
      externalUpdateCount.current += 1;
      view.dispatch({
        changes: { from: 0, to: view.state.doc.length, insert: ontologyTtl },
      });
    }
  }, [ontologyTtl]);

  const handleImportTtl = useCallback(() => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ".ttl,.owl,.turtle";
    input.onchange = async () => {
      const file = input.files?.[0];
      if (!file) return;
      const text = await file.text();
      onChangeRef.current(text);
      const view = viewRef.current;
      if (view) {
        externalUpdateCount.current += 1;
        view.dispatch({
          changes: { from: 0, to: view.state.doc.length, insert: text },
        });
      }
    };
    input.click();
  }, []);

  const handleExportTtl = useCallback(() => {
    const view = viewRef.current;
    if (!view) return;
    const blob = new Blob([view.state.doc.toString()], { type: "text/turtle" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "ontology.ttl";
    a.click();
    URL.revokeObjectURL(url);
  }, []);

  return (
    <div className="h-full flex flex-col">
      <div className="flex items-center justify-between px-3 py-2 border-b border-gray-700 bg-gray-900/50">
        <div className="flex items-center gap-2">
          <button
            onClick={() => setViewMode("text")}
            className={`text-xs font-medium uppercase tracking-wide transition-colors ${viewMode === "text" ? "text-indigo-400" : "text-gray-500 hover:text-gray-300"}`}
          >
            Turtle
          </button>
          <span className="text-gray-700">|</span>
          <button
            onClick={() => setViewMode("graph")}
            className={`text-xs font-medium uppercase tracking-wide transition-colors ${viewMode === "graph" ? "text-indigo-400" : "text-gray-500 hover:text-gray-300"}`}
          >
            Graph
          </button>
        </div>
        <div className="flex items-center gap-1.5">
          <button
            onClick={handleImportTtl}
            className="px-2 py-0.5 text-[10px] rounded bg-gray-700 text-gray-400 hover:text-gray-200 transition-colors"
            title="Import OWL/Turtle ontology from file"
          >
            Import
          </button>
          <button
            onClick={handleExportTtl}
            className="px-2 py-0.5 text-[10px] rounded bg-gray-700 text-gray-400 hover:text-gray-200 transition-colors"
            title="Download current ontology as Turtle"
          >
            Export
          </button>
          {onClose && (
            <>
              <span className="w-px h-4 bg-gray-700 mx-0.5" />
              <button
                onClick={onClose}
                className="px-1.5 py-0.5 text-xs leading-none rounded text-gray-500 hover:text-gray-200 hover:bg-gray-700 transition-colors"
                title="Hide ontology pane (more room for queries)"
                aria-label="Hide ontology pane"
              >
                &#9664;
              </button>
            </>
          )}
        </div>
      </div>
      <div
        className="flex-1 min-h-0"
        style={{ display: viewMode === "graph" ? "block" : "none" }}
      >
        <SchemaGraph ontologyTtl={ontologyTtl} />
      </div>
      <div
        className="flex-1 min-h-0"
        ref={containerRef}
        style={{ display: viewMode === "text" ? "block" : "none" }}
      />
    </div>
  );
}
