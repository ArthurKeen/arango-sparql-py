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
import SchemaWarningBanner from "./components/SchemaWarningBanner";
import GraphSelector from "./components/GraphSelector";
import { useAppState } from "./api/store";
import {
  translateSparql,
  executeSparql,
  nl2Sparql,
  suggestNlQueries,
  listGraphs,
  bindGraph,
  isAuthError,
  schemaForceReacquire,
  type GraphInfo,
} from "./api/client";

// App.tsx mirrors the layout of arango-cypher-py/ui/src/App.tsx but
// trimmed to the surface the SPARQL backend actually exposes today:
//
//   * /translate    — required, serves the editor preview.
//   * /execute      — requires a connected ArangoDB session.
//   * /connect      — wired through ConnectionDialog.
//   * /nl-translate — natural-language "Ask" bar (LLM → SPARQL → AQL).
//                     Returns both the SPARQL and ready-to-run AQL in a
//                     single call, so "Generate" fills the editor and
//                     the user runs it with the existing Run button.
//
// Endpoints the Cypher UI calls but the SPARQL backend has not yet
// shipped (learn corrections, explain/profile, tenant catalogue,
// per-session named-graph scope) are intentionally left out — adding
// their UI before the backend exists would surface as unconditional
// 404s and 422s in the browser console. They land behind their
// respective backend milestones (graph selection: PRD named-graph
// phase; NL suggestions: /nl-samples).

export default function App() {
  const [state, dispatch] = useAppState();
  const [showMapping, setShowMapping] = useState(true);
  const [mappingWidth, setMappingWidth] = useState(320);
  const [showHistory, setShowHistory] = useState(false);
  const [showSamples, setShowSamples] = useState(false);
  const [showOutline, setShowOutline] = useState(false);
  const [nlInput, setNlInput] = useState("");
  const [showNlSuggestions, setShowNlSuggestions] = useState(false);
  const [nlSamples, setNlSamples] = useState<string[]>([]);
  const [graphs, setGraphs] = useState<GraphInfo[]>([]);
  const [graphScope, setGraphScope] = useState<string | null>(null);
  const [graphBusy, setGraphBusy] = useState(false);
  const [graphError, setGraphError] = useState<string | null>(null);
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

  // Schema-derived "Ask" suggestions (POST /nl-samples). Re-fetched only
  // when the schema source actually changes — keyed on (url, db,
  // ontology) — so editing an unrelated bit of state doesn't spam the
  // endpoint. Rule-based on the backend, so this works without an LLM
  // provider; failures are swallowed (suggestions are best-effort).
  const lastSampleKeyRef = useRef<string | null>(null);
  useEffect(() => {
    const ttl = state.ontologyTtl.trim();
    if (!ttl) {
      lastSampleKeyRef.current = null;
      setNlSamples([]);
      return;
    }
    const key = `${state.connection.url}||${state.connection.database}||${ttl.length}:${ttl.slice(0, 256)}`;
    if (lastSampleKeyRef.current === key) return;
    lastSampleKeyRef.current = key;
    let cancelled = false;
    void (async () => {
      try {
        const resp = await suggestNlQueries(ttl, 8, true);
        if (!cancelled) setNlSamples(resp.queries ?? []);
      } catch {
        // Best-effort: leave whatever samples we already have.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [state.ontologyTtl, state.connection.url, state.connection.database]);

  // Load the connected database's named graphs so the scope picker can
  // offer them. Resets the scope on every (re)connect. Best-effort: a DB
  // with no graphs yields an empty list and the picker stays hidden.
  useEffect(() => {
    const token = state.connection.token;
    if (!token) {
      setGraphs([]);
      setGraphScope(null);
      setGraphError(null);
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const resp = await listGraphs(token);
        if (!cancelled) {
          setGraphs(resp.graphs ?? []);
          setGraphScope(null);
          setGraphError(null);
        }
      } catch (err) {
        if (!cancelled) {
          setGraphs([]);
          setGraphError(err instanceof Error ? err.message : String(err));
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [state.connection.token, state.connection.url, state.connection.database]);
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

  const handleRefreshSchema = useCallback(async () => {
    if (!state.connection.token) return;
    dispatch({ type: "SCHEMA_REFRESH_START" });
    try {
      const resp = await schemaForceReacquire(state.connection.token, {
        database: state.connection.database,
        include_owl: true,
        include_statistics: true,
      });
      dispatch({
        type: "SCHEMA_LOADED",
        mapping: resp.mapping ?? null,
        summary: resp.summary ?? null,
        warnings: (resp.warnings ?? []).map((w) => ({
          code: w.code,
          message: w.message,
          install_hint: w.install_hint,
        })),
        cacheHit: false,
      });
      // If the analyzer emitted inline OWL, prefill the editor so
      // the user sees the freshly-acquired ontology without
      // hand-import. Both spellings honoured (camelCase from the
      // wire, snake_case from a Python-emitted bundle).
      const mapping = resp.mapping as Record<string, unknown> | undefined;
      const owl =
        (mapping?.owlTurtle as string | undefined) ??
        (mapping?.owl_turtle as string | undefined);
      if (typeof owl === "string" && owl.trim().length > 0) {
        dispatch({ type: "SET_ONTOLOGY_TTL", ontologyTtl: owl });
      }
    } catch (err) {
      dispatch({
        type: "SCHEMA_REFRESH_ERROR",
        error: err instanceof Error ? err.message : String(err),
      });
      handleMaybeAuthError(err);
    }
  }, [
    dispatch,
    state.connection.token,
    state.connection.database,
    handleMaybeAuthError,
  ]);

  // Bind (or clear) the named-graph scope, then re-acquire the schema so
  // the ontology / mapping / NL suggestions reflect the narrowed set of
  // collections. `null` clears the scope back to "all collections".
  const handleSelectGraph = useCallback(
    async (graphName: string | null) => {
      const token = state.connection.token;
      if (!token) return;
      setGraphBusy(true);
      setGraphError(null);
      try {
        await bindGraph(graphName, token);
        setGraphScope(graphName);
        await handleRefreshSchema();
      } catch (err) {
        setGraphError(err instanceof Error ? err.message : String(err));
        handleMaybeAuthError(err);
      } finally {
        setGraphBusy(false);
      }
    },
    [state.connection.token, handleRefreshSchema, handleMaybeAuthError],
  );

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

  // Natural-language "Ask" → /nl-translate. One call returns BOTH the
  // SPARQL (shown in the editor) and the ready-to-run AQL preview; the
  // user then hits Run (which executes the generated SPARQL through the
  // existing /execute path). No DB connection is required to generate.
  const handleNL = useCallback(async () => {
    const question = nlInput.trim();
    if (!question) return;
    setShowNlSuggestions(false);
    dispatch({ type: "NL_START", question });
    try {
      const resp = await nl2Sparql(question, {
        ontologyTtl: ontologyRef.current || undefined,
      });
      dispatch({
        type: "NL_SUCCESS",
        sparql: resp.sparql,
        aql: resp.aql,
        bindVars: resp.bind_vars,
        warnings: (resp.warnings ?? [])
          .map((w) => ({
            message: String((w as { message?: unknown }).message ?? ""),
          }))
          .filter((w) => w.message.length > 0),
        latencyMs: resp.latency_ms,
        llmCalls: resp.llm_calls,
        costUsd: resp.cost_usd,
        repaired: resp.repaired,
      });
      if (resp.sparql.trim()) {
        dispatch({
          type: "ADD_HISTORY",
          entry: {
            sparql: resp.sparql,
            timestamp: Date.now(),
            aqlPreview: resp.aql.slice(0, 120),
          },
        });
      }
    } catch (err) {
      dispatch({
        type: "NL_ERROR",
        error: err instanceof Error ? err.message : String(err),
      });
      handleMaybeAuthError(err);
    }
  }, [nlInput, dispatch, handleMaybeAuthError]);

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
  const isLoading = state.translating || state.executing || state.generating;
  // Ask-bar suggestions: recent questions first, then schema-derived
  // example queries (POST /nl-samples), de-duplicated case-insensitively
  // and capped. Tagged with `kind` so the dropdown can label "recent" vs
  // "example".
  const nlSuggestions = (() => {
    const seen = new Set<string>();
    const merged: Array<{ text: string; kind: "recent" | "example" }> = [];
    for (const text of state.nlHistory) {
      const key = text.toLowerCase();
      if (text.trim() && !seen.has(key)) {
        seen.add(key);
        merged.push({ text, kind: "recent" });
      }
    }
    for (const text of nlSamples) {
      const key = text.toLowerCase();
      if (text.trim() && !seen.has(key)) {
        seen.add(key);
        merged.push({ text, kind: "example" });
      }
    }
    return merged.slice(0, 10);
  })();

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
            onSchemaLoaded={(turtle) => {
              if (turtle) {
                dispatch({ type: "SET_ONTOLOGY_TTL", ontologyTtl: turtle });
              }
            }}
          />
          {isConnected && (
            <GraphSelector
              graphs={graphs}
              selection={graphScope}
              loading={graphBusy}
              onSelect={handleSelectGraph}
              error={graphError}
            />
          )}
          {isConnected && (
            <button
              onClick={handleRefreshSchema}
              disabled={state.schema.refreshing}
              className="px-2 py-1 text-[11px] rounded bg-gray-800 text-gray-400 hover:text-gray-200 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              title="Force re-acquire of the schema mapping (POST /schema/force-reacquire)"
            >
              {state.schema.refreshing ? "Refreshing\u2026" : "Refresh schema"}
            </button>
          )}
          {state.schema.cacheHit && !state.schema.refreshing && (
            <span
              className="text-[10px] text-gray-500 tabular-nums"
              title="Last /schema/introspect was served from the L1 cache"
            >
              cached
            </span>
          )}
          {state.schema.lastImportTripleCount != null && (
            <span
              className="text-[10px] text-gray-500 tabular-nums"
              title="Triples in the most recently imported OWL ontology"
            >
              {state.schema.lastImportTripleCount} triples
            </span>
          )}
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

      {/* Schema-acquisition warnings (PRD §10.2). Sits above the main
          editor so analyzer-not-installed and W_SCHEMA_* messages are
          visible without scrolling. The banner uses a per-(url, db,
          code) dismissed-set so a returning user does not re-see
          warnings they have already acknowledged. */}
      <SchemaWarningBanner
        warnings={state.schema.warnings}
        url={state.connection.url}
        database={state.connection.database}
        token={state.connection.token}
        dispatch={dispatch}
      />
      {state.schema.error && (
        <div className="px-4 py-1 bg-amber-900/20 border-b border-amber-800/30 flex items-center justify-between gap-3">
          <span className="text-xs text-amber-300 flex-1 break-words">
            <strong className="font-medium">Schema:</strong> {state.schema.error}
          </span>
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
                sessionToken={state.connection.token}
                dispatch={dispatch}
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
          {/* Natural-language "Ask" bar. Mirrors arango-cypher-py's NL
              input: type a question, Generate (or Enter) calls
              /nl-translate, and the returned SPARQL drops into the
              editor with the AQL preview ready to Run. */}
          <div className="relative flex items-center gap-2 px-3 py-2 bg-gray-900/70 border-b border-gray-800">
            <span className="text-xs font-medium text-gray-400 shrink-0">
              Ask:
            </span>
            <div className="relative flex-1 min-w-0">
              <input
                type="text"
                value={nlInput}
                onChange={(e) => setNlInput(e.target.value)}
                onFocus={() => setShowNlSuggestions(true)}
                onBlur={() =>
                  window.setTimeout(() => setShowNlSuggestions(false), 120)
                }
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    void handleNL();
                  } else if (e.key === "Escape") {
                    setShowNlSuggestions(false);
                  }
                }}
                placeholder={"Describe what you want in plain English\u2026"}
                aria-label="Natural-language question"
                className="w-full px-2.5 py-1.5 text-xs rounded bg-gray-800 text-gray-100 placeholder-gray-500 border border-gray-700 focus:border-violet-500 focus:outline-none"
              />
              {showNlSuggestions && nlSuggestions.length > 0 && (
                <ul className="absolute z-20 left-0 right-0 mt-1 max-h-60 overflow-y-auto rounded border border-gray-700 bg-gray-900 shadow-lg">
                  {nlSuggestions.map((s, i) => (
                    <li key={`${i}-${s.text}`}>
                      <button
                        type="button"
                        onMouseDown={(e) => {
                          // onMouseDown (not onClick) so the pick lands
                          // before the input's onBlur closes the list.
                          e.preventDefault();
                          setNlInput(s.text);
                          setShowNlSuggestions(false);
                        }}
                        className="flex w-full items-center gap-2 px-2.5 py-1.5 text-xs text-gray-300 hover:bg-gray-800"
                        title={s.text}
                      >
                        <span
                          className={`shrink-0 text-[9px] uppercase tracking-wider ${
                            s.kind === "recent"
                              ? "text-gray-500"
                              : "text-violet-400/70"
                          }`}
                        >
                          {s.kind === "recent" ? "recent" : "example"}
                        </span>
                        <span className="truncate">{s.text}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <button
              onClick={handleNL}
              disabled={state.generating || !nlInput.trim()}
              className="px-3 py-1.5 text-xs font-medium rounded bg-violet-600 hover:bg-violet-500 text-white transition-colors disabled:opacity-40 disabled:cursor-not-allowed shrink-0"
              title="Generate SPARQL from your question (Enter)"
            >
              {state.generating ? "Generating\u2026" : "Generate"}
            </button>
            {state.generating && (
              <div className="w-4 h-4 border-2 border-violet-400 border-t-transparent rounded-full animate-spin shrink-0" />
            )}
            {!state.generating &&
              state.nlInfo &&
              state.sparqlSource === "nl_pipeline" && (
                <span
                  className="text-[10px] text-emerald-500/70 tabular-nums shrink-0"
                  title="Generated from your natural-language question"
                >
                  from NL &middot; {state.nlInfo.latencyMs}ms &middot;{" "}
                  {state.nlInfo.llmCalls} call
                  {state.nlInfo.llmCalls === 1 ? "" : "s"}
                  {state.nlInfo.repaired ? " \u00b7 repaired" : ""}
                </span>
              )}
          </div>

          {state.nlError && (
            <div className="px-3 py-1.5 bg-red-900/30 border-b border-red-800 flex items-center justify-between gap-3">
              <span className="text-xs text-red-300 flex-1 break-words">
                {state.nlError}
              </span>
              <button
                onClick={() => dispatch({ type: "CLEAR_NL_ERROR" })}
                className="text-red-400 hover:text-red-200 text-[11px] shrink-0"
              >
                Dismiss
              </button>
            </div>
          )}

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
