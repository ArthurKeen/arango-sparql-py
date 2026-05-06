import { useCallback, useEffect, useRef, useState } from "react";
import type { EditorView } from "@codemirror/view";
import ConnectionDialog from "./components/ConnectionDialog";
import SparqlEditor from "./components/SparqlEditor";
import AqlEditor from "./components/AqlEditor";
import MappingPanel from "./components/MappingPanel";
import ParameterPanel from "./components/ParameterPanel";
import QueryHistory from "./components/QueryHistory";
import SampleQueries from "./components/SampleQueries";
import ClauseOutline from "./components/ClauseOutline";
import ResultsPanel from "./components/ResultsPanel";
import { useAppState } from "./api/store";
import {
  translateSparql,
  executeSparql,
  isAuthError,
} from "./api/client";

// App.tsx mirrors the layout of arango-cypher-py/ui/src/App.tsx but
// trimmed to the surface the SPARQL backend actually exposes today:
//
//   * /translate — required, serves the editor preview.
//   * /execute   — requires a connected ArangoDB session.
//   * /connect   — wired through ConnectionDialog.
//
// Endpoints the Cypher UI calls but the SPARQL backend has not yet
// shipped (NL pipeline, learn corrections, explain/profile, tenant
// catalogue, schema introspection warnings) are intentionally left
// out — adding their UI before the backend exists would surface as
// unconditional 404s and 422s in the browser console. They will land
// behind their respective backend milestones.

export default function App() {
  const [state, dispatch] = useAppState();
  const [showMapping, setShowMapping] = useState(true);
  const [mappingWidth, setMappingWidth] = useState(320);
  const [showHistory, setShowHistory] = useState(false);
  const [showSamples, setShowSamples] = useState(false);
  const [showOutline, setShowOutline] = useState(false);
  const sparqlViewRef = useRef<EditorView | null>(null);

  const dragRef = useRef<{ startX: number; startW: number } | null>(null);
  useEffect(() => {
    const onMouseMove = (e: MouseEvent) => {
      if (!dragRef.current) return;
      const delta = e.clientX - dragRef.current.startX;
      setMappingWidth(Math.max(240, Math.min(800, dragRef.current.startW + delta)));
    };
    const onMouseUp = () => {
      dragRef.current = null;
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup", onMouseUp);
    return () => {
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup", onMouseUp);
    };
  }, []);

  const sparqlRef = useRef(state.sparql);
  sparqlRef.current = state.sparql;
  const ontologyRef = useRef(state.ontologyTtl);
  ontologyRef.current = state.ontologyTtl;
  const paramsRef = useRef(state.params);
  paramsRef.current = state.params;

  const buildRequest = useCallback(() => {
    const p = paramsRef.current;
    return {
      sparql: sparqlRef.current,
      ontology_ttl: ontologyRef.current || undefined,
      params: Object.keys(p).length > 0 ? p : undefined,
    };
  }, []);

  const addToHistory = useCallback(
    (aqlPreview: string) => {
      const sparql = sparqlRef.current.trim();
      if (!sparql) return;
      dispatch({
        type: "ADD_HISTORY",
        entry: {
          sparql,
          timestamp: Date.now(),
          aqlPreview: aqlPreview.slice(0, 120),
        },
      });
    },
    [dispatch],
  );

  const handleMaybeAuthError = useCallback(
    (err: unknown) => {
      if (isAuthError(err)) {
        dispatch({ type: "DISCONNECT" });
      }
    },
    [dispatch],
  );

  const handleTranslate = useCallback(async () => {
    if (!sparqlRef.current.trim()) return;
    dispatch({ type: "TRANSLATE_START" });
    try {
      const resp = await translateSparql(buildRequest());
      dispatch({
        type: "TRANSLATE_SUCCESS",
        aql: resp.aql,
        bindVars: resp.bind_vars,
        warnings: resp.warnings,
        translateMs: resp.elapsed_ms ?? null,
      });
      addToHistory(resp.aql);
    } catch (err) {
      dispatch({
        type: "TRANSLATE_ERROR",
        error: err instanceof Error ? err.message : String(err),
      });
      handleMaybeAuthError(err);
    }
  }, [dispatch, buildRequest, addToHistory, handleMaybeAuthError]);

  const handleExecute = useCallback(async () => {
    if (!state.connection.token) return;
    if (!sparqlRef.current.trim()) return;
    dispatch({ type: "EXECUTE_START" });
    try {
      const resp = await executeSparql(buildRequest(), state.connection.token);
      if (resp.aql) {
        dispatch({
          type: "TRANSLATE_SUCCESS",
          aql: resp.aql,
          bindVars: resp.bind_vars ?? {},
          warnings: resp.warnings,
          translateMs: null,
        });
      }
      dispatch({
        type: "EXECUTE_SUCCESS",
        results: resp.bindings,
        warnings: resp.warnings,
        execMs: resp.elapsed_ms ?? null,
      });
      if (resp.aql) addToHistory(resp.aql);
    } catch (err) {
      dispatch({
        type: "EXECUTE_ERROR",
        error: err instanceof Error ? err.message : String(err),
      });
      handleMaybeAuthError(err);
    }
  }, [
    dispatch,
    buildRequest,
    addToHistory,
    state.connection.token,
    handleMaybeAuthError,
  ]);

  const handleJumpToLine = useCallback((line: number) => {
    const view = sparqlViewRef.current;
    if (!view) return;
    const lineInfo = view.state.doc.line(Math.min(line, view.state.doc.lines));
    view.dispatch({
      selection: { anchor: lineInfo.from },
      scrollIntoView: true,
    });
    view.focus();
  }, []);

  const isConnected = state.connection.status === "connected";
  const isLoading = state.translating || state.executing;

  return (
    <div className="h-screen flex flex-col bg-gray-950 text-gray-100">
      <header className="flex items-center justify-between px-4 py-2 bg-gray-900 border-b border-gray-800">
        <div className="flex items-center gap-3">
          <h1 className="text-sm font-semibold text-white tracking-tight">
            Arango SPARQL
          </h1>
          <span className="text-gray-600 text-xs">|</span>
          <ConnectionDialog
            connection={state.connection}
            dispatch={dispatch}
          />
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowSamples(true)}
            className="px-2.5 py-1 text-xs rounded bg-gray-800 text-gray-400 hover:text-gray-200 transition-colors"
          >
            Samples
          </button>
          <button
            onClick={() => setShowHistory(true)}
            className="px-2.5 py-1 text-xs rounded bg-gray-800 text-gray-400 hover:text-gray-200 transition-colors"
          >
            History
            {state.history.length > 0 && (
              <span className="ml-1.5 text-gray-500">
                ({state.history.length})
              </span>
            )}
          </button>
          <button
            onClick={() => setShowOutline((v) => !v)}
            className={`px-2.5 py-1 text-xs rounded transition-colors ${
              showOutline
                ? "bg-indigo-600/20 text-indigo-400 border border-indigo-600/30"
                : "bg-gray-800 text-gray-400 hover:text-gray-200"
            }`}
          >
            Outline
          </button>
          <button
            onClick={() => setShowMapping((v) => !v)}
            className={`px-2.5 py-1 text-xs rounded transition-colors ${
              showMapping
                ? "bg-indigo-600/20 text-indigo-400 border border-indigo-600/30"
                : "bg-gray-800 text-gray-400 hover:text-gray-200"
            }`}
          >
            Ontology
          </button>
        </div>
      </header>

      {state.error && (
        <div className="px-4 py-2 bg-red-900/30 border-b border-red-800 flex items-center justify-between gap-3">
          <span className="text-sm text-red-300 flex-1 break-words">
            {state.error}
          </span>
          <button
            onClick={() => dispatch({ type: "CLEAR_ERROR" })}
            className="text-red-400 hover:text-red-200 text-xs shrink-0"
          >
            Dismiss
          </button>
        </div>
      )}

      <div className="flex-1 min-h-0 flex">
        {showMapping ? (
          <>
            <div
              className="border-r border-gray-800 flex-shrink-0 relative"
              style={{ width: mappingWidth }}
            >
              <MappingPanel
                ontologyTtl={state.ontologyTtl}
                onChange={(ttl) =>
                  dispatch({ type: "SET_ONTOLOGY_TTL", ontologyTtl: ttl })
                }
                onClose={() => setShowMapping(false)}
              />
            </div>
            <div
              className="w-1.5 flex-shrink-0 cursor-col-resize hover:bg-indigo-500/30 active:bg-indigo-500/40 transition-colors"
              onMouseDown={(e) => {
                e.preventDefault();
                dragRef.current = { startX: e.clientX, startW: mappingWidth };
                document.body.style.cursor = "col-resize";
                document.body.style.userSelect = "none";
              }}
            />
          </>
        ) : (
          <button
            onClick={() => setShowMapping(true)}
            title="Show ontology pane"
            aria-label="Show ontology pane"
            className="w-6 flex-shrink-0 flex flex-col items-center justify-center gap-2 bg-gray-900/40 hover:bg-gray-800 border-r border-gray-800 group transition-colors"
          >
            <span className="text-gray-500 group-hover:text-indigo-400 text-xs leading-none transition-colors">
              &#9654;
            </span>
            <span
              className="text-[10px] text-gray-600 group-hover:text-gray-400 uppercase tracking-wider transition-colors"
              style={{ writingMode: "vertical-rl", transform: "rotate(180deg)" }}
            >
              Ontology
            </span>
          </button>
        )}

        <div className="flex-1 min-w-0 flex flex-col">
          <div className="flex items-center gap-2 px-3 py-2 bg-gray-900/50 border-b border-gray-800">
            <button
              onClick={handleTranslate}
              disabled={isLoading || !state.sparql.trim()}
              className="px-3 py-1.5 text-xs font-medium rounded bg-indigo-600 hover:bg-indigo-500 text-white transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
              title="Ctrl/Cmd+Enter"
            >
              {state.translating ? "Translating..." : "Translate"}
            </button>
            <button
              onClick={handleExecute}
              disabled={isLoading || !isConnected || !state.sparql.trim()}
              className="px-3 py-1.5 text-xs font-medium rounded bg-emerald-600 hover:bg-emerald-500 text-white transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
              title="Shift+Enter"
            >
              {state.executing ? "Running..." : "Run"}
            </button>
            {isLoading && (
              <div className="ml-2 w-4 h-4 border-2 border-indigo-400 border-t-transparent rounded-full animate-spin" />
            )}
            <div className="flex-1" />
            <span className="text-xs text-gray-600">
              {isConnected ? (
                <span className="text-gray-500">Shift+Enter to run</span>
              ) : (
                <span className="text-amber-600">Connect to ArangoDB to run</span>
              )}
            </span>
          </div>

          <div className="flex-1 min-h-0 flex">
            <div className="flex-1 min-w-0 flex flex-col border-r border-gray-800">
              <div className="px-3 py-1.5 bg-gray-900/30 border-b border-gray-800">
                <span className="text-xs font-medium text-gray-400 uppercase tracking-wide">
                  SPARQL
                </span>
              </div>
              <div className="flex-1 min-h-0 flex">
                <div className="flex-1 min-w-0">
                  <SparqlEditor
                    value={state.sparql}
                    onChange={(v) => dispatch({ type: "SET_SPARQL", sparql: v })}
                    onTranslate={handleTranslate}
                    onExecute={handleExecute}
                    viewRef={sparqlViewRef}
                  />
                </div>
                {showOutline && (
                  <div className="w-48 border-l border-gray-800 overflow-y-auto bg-gray-900/30 shrink-0">
                    <div className="px-3 py-1.5 border-b border-gray-800">
                      <span className="text-[10px] font-semibold text-gray-500 uppercase tracking-wide">
                        Clause Outline
                      </span>
                    </div>
                    <ClauseOutline
                      sparql={state.sparql}
                      onJumpToLine={handleJumpToLine}
                    />
                  </div>
                )}
              </div>
              <ParameterPanel
                sparql={state.sparql}
                params={state.params}
                onChange={(p) => dispatch({ type: "SET_PARAMS", params: p })}
              />
            </div>

            <div className="flex-1 min-w-0 flex flex-col">
              <div className="px-3 py-1.5 bg-gray-900/30 border-b border-gray-800 flex items-center gap-2">
                <span className="text-xs font-medium text-gray-400 uppercase tracking-wide">
                  AQL
                </span>
                <div className="flex-1" />
                {state.translateMs != null && (
                  <span className="text-[10px] text-emerald-500/70 tabular-nums">
                    SPARQL→AQL {state.translateMs}ms
                  </span>
                )}
                {state.execMs != null && (
                  <span className="text-[10px] text-sky-400/70 tabular-nums">
                    AQL exec {state.execMs}ms
                  </span>
                )}
              </div>
              {state.warnings.length > 0 && (
                <div className="px-3 py-1.5 bg-amber-900/20 border-b border-amber-800/30 space-y-0.5">
                  {state.warnings.map((w, i) => (
                    <div key={i} className="flex items-start gap-2">
                      <span className="text-amber-500 text-xs mt-0.5 shrink-0">
                        &#9888;
                      </span>
                      <span className="text-xs text-amber-400">{w.message}</span>
                    </div>
                  ))}
                </div>
              )}
              <div className="flex-1 min-h-0">
                <AqlEditor
                  value={state.aql}
                  bindVars={state.bindVars}
                  error={null}
                />
              </div>
            </div>
          </div>

          <div className="h-72 border-t border-gray-800 flex-shrink-0">
            <ResultsPanel
              results={state.results}
              warnings={state.warnings}
              activeTab={state.activeResultTab}
              dispatch={dispatch}
              execMs={state.execMs}
            />
          </div>
        </div>
      </div>

      {showHistory && (
        <QueryHistory
          history={state.history}
          onSelect={(sparql) => dispatch({ type: "SET_SPARQL", sparql })}
          onClear={() => dispatch({ type: "CLEAR_HISTORY" })}
          onClose={() => setShowHistory(false)}
        />
      )}

      {showSamples && (
        <SampleQueries
          onSelect={(sparql) => dispatch({ type: "SET_SPARQL", sparql })}
          onClose={() => setShowSamples(false)}
        />
      )}
    </div>
  );
}
