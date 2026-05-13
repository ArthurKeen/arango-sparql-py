import { useCallback, useEffect, useRef, useState } from "react";
import { EditorState } from "@codemirror/state";
import { EditorView, lineNumbers, keymap } from "@codemirror/view";
import { defaultKeymap, history, historyKeymap } from "@codemirror/commands";
import { bracketMatching } from "@codemirror/language";
import { closeBrackets, closeBracketsKeymap } from "@codemirror/autocomplete";
import { oneDark } from "./theme";
import SchemaGraph from "./SchemaGraph";
import {
  ApiError,
  exportOwlAsTurtle,
  importOwl,
} from "../api/client";
import type { Action } from "../api/store";

// "Mapping" panel for the SPARQL UI. Where the Cypher project edits a
// JSON schema-mapping object, the SPARQL service expects an OWL/Turtle
// ontology body sent as `ontology_ttl` on /translate. The component
// retains the same name + slot in the layout so a developer who reads
// the Cypher UI can find it instantly — the contents are now a plain
// CodeMirror Turtle text editor with a "Graph" toggle that defers to
// `SchemaGraph` (placeholder until `/schema/owl` lands).
//
// Import / Export buttons are server-backed (PRD §6.4 rows 8 & 9):
//
// * Import → POST /mapping/import-owl with the file contents. The
//   backend's OWL-bomb defences (§8.6 T7) catch malformed or
//   oversized OWL bodies before they hit our editor; on success we
//   replace the editor contents with the canonical Turtle the
//   importer normalised.
// * Export → POST /mapping/export-owl with `Accept: text/turtle`.
//   This goes through the synthesizer so the downloaded ontology
//   includes the same `phys:*` annotations a freshly-acquired
//   bundle would carry.
//
// Both paths fall back to the local-file behaviour when there is
// no active session — a disconnected user can still load a Turtle
// blob into the editor and copy the editor text out via standard
// browser select-all + save.

interface Props {
  ontologyTtl: string;
  onChange: (ttl: string) => void;
  onClose?: () => void;
  /** Active session token; when null, Import/Export degrade to
   * pure-local behaviour (file picker / blob download of the
   * editor contents). */
  sessionToken?: string | null;
  /** Optional dispatch — used to surface OWL-bomb / parse errors as
   * top-of-app errors and to record the imported triple count
   * (`SCHEMA_IMPORT_SUCCESS`). When not provided the panel
   * gracefully degrades to console.warn. */
  dispatch?: (action: Action) => void;
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

export default function MappingPanel({
  ontologyTtl,
  onChange,
  onClose,
  sessionToken,
  dispatch,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView | null>(null);
  const [viewMode, setViewMode] = useState<"text" | "graph">("text");
  const [busy, setBusy] = useState<"import" | "export" | null>(null);
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;
  const tokenRef = useRef(sessionToken);
  tokenRef.current = sessionToken;
  const dispatchRef = useRef(dispatch);
  dispatchRef.current = dispatch;

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

  const replaceEditor = useCallback((text: string) => {
    onChangeRef.current(text);
    const view = viewRef.current;
    if (view) {
      externalUpdateCount.current += 1;
      view.dispatch({
        changes: { from: 0, to: view.state.doc.length, insert: text },
      });
    }
  }, []);

  const handleImportTtl = useCallback(() => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ".ttl,.owl,.turtle";
    input.onchange = async () => {
      const file = input.files?.[0];
      if (!file) return;
      const text = await file.text();
      const token = tokenRef.current;
      if (!token) {
        // Disconnected — local-file fallback. The editor is the
        // only source of truth in this case; the AQL preview
        // already takes the editor contents on Translate.
        replaceEditor(text);
        return;
      }
      setBusy("import");
      try {
        const resp = await importOwl(text, token, `imported file: ${file.name}`);
        // Server-canonical Turtle wins — it's been normalised by
        // `mapping_to_turtle` in the round-trip path of the
        // backend's MappingBundle. Falls back to the file text
        // when the response did not include the OWL.
        const mappingTurtle =
          (resp.mapping?.owlTurtle as string | undefined) ??
          (resp.mapping?.owl_turtle as string | undefined) ??
          text;
        replaceEditor(mappingTurtle);
        dispatchRef.current?.({
          type: "SCHEMA_IMPORT_SUCCESS",
          tripleCount: resp.triple_count,
        });
      } catch (err) {
        const message =
          err instanceof ApiError
            ? `Import failed: ${err.message}`
            : err instanceof Error
              ? err.message
              : String(err);
        // No CLEAR_ERROR / dedicated SCHEMA_IMPORT_ERROR action —
        // surfacing the failure via the existing top-of-app error
        // banner (TRANSLATE_ERROR's slot) keeps the UI symmetric
        // with the analyzer-warning path.
        dispatchRef.current?.({
          type: "SCHEMA_REFRESH_ERROR",
          error: message,
        });
        console.error("OWL import failed:", err);
      } finally {
        setBusy(null);
      }
    };
    input.click();
  }, [replaceEditor]);

  const handleExportTtl = useCallback(async () => {
    const view = viewRef.current;
    if (!view) return;
    const editorText = view.state.doc.toString();
    const token = tokenRef.current;
    let downloadable: string;
    let filename = "ontology.ttl";
    if (!token) {
      // Disconnected — local download of the editor contents.
      downloadable = editorText;
    } else {
      setBusy("export");
      try {
        const resp = await exportOwlAsTurtle(null, editorText, token);
        downloadable = resp.turtle;
        filename = `ontology-${resp.tripleCount}-triples.ttl`;
      } catch (err) {
        // Export failures fall through to a local download of
        // exactly what's in the editor — the user still gets
        // their bytes, just without server-side normalisation.
        console.warn("OWL export via backend failed; falling back to local:", err);
        downloadable = editorText;
      } finally {
        setBusy(null);
      }
    }
    const blob = new Blob([downloadable], { type: "text/turtle" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
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
            disabled={busy !== null}
            className="px-2 py-0.5 text-[10px] rounded bg-gray-700 text-gray-400 hover:text-gray-200 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            title={
              sessionToken
                ? "Import OWL/Turtle ontology — parsed by /mapping/import-owl with OWL-bomb defences"
                : "Import OWL/Turtle ontology from file (local; connect to validate via backend)"
            }
          >
            {busy === "import" ? "Importing\u2026" : "Import"}
          </button>
          <button
            onClick={handleExportTtl}
            disabled={busy !== null}
            className="px-2 py-0.5 text-[10px] rounded bg-gray-700 text-gray-400 hover:text-gray-200 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            title={
              sessionToken
                ? "Download current ontology as Turtle (rendered by /mapping/export-owl)"
                : "Download editor contents as Turtle (local; connect to round-trip through backend)"
            }
          >
            {busy === "export" ? "Exporting\u2026" : "Export"}
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
