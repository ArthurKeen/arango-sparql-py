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
import ChatComposer from "./components/ChatComposer";
import QueryInspector from "./components/QueryInspector";
import SettingsMenu from "./components/SettingsMenu";
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
import {
  planSend,
  currentStage,
  stageLabel,
  isBusy as pipelineBusy,
} from "./utils/pipeline";

// App.tsx implements the chat-first "Query Workbench Shell" (PRD §10.0),
// mirroring `references/arango-cypher-py/ui/src/App.tsx`:
//
//   * L0 — ChatComposer (NL "Ask") + ResultsPanel are always visible.
//   * L1 — QueryInspector (SPARQL | AQL editors + power actions) is a
//     collapsible bottom drawer, closed by default.
//   * L2 — Ontology, outline, samples, history live behind the gear
//     (SettingsMenu); the ontology panel is closed by default.
//
// Backend surface used today:
//   * /nl-translate — Send's generate step (NL → SPARQL → AQL in one call)
//   * /translate    — inspector "Translate" for hand-written SPARQL
//   * /execute      — Send's run step + inspector "Run" (needs a session)
//   * /connect, /graphs, /session/graph, /schema/force-reacquire, /nl-samples
//
// Explain/Profile actions in the inspector land with WP-UI-EXPLAIN once
// the results panel renders plan trees.

function loadBool(key: string, fallback: boolean): boolean {
  try {
    const raw = localStorage.getItem(key);
    return raw == null ? fallback : raw === "true";
  } catch {
    return fallback;
  }
}

export default function App() {
  const [state, dispatch] = useAppState();
  // L2 panels — progressive disclosure: all closed by default so a hard
  // refresh returns to the clean L0 (composer + results) surface.
  const [showMapping, setShowMapping] = useState(false);
  const [mappingWidth, setMappingWidth] = useState(320);
  const [showHistory, setShowHistory] = useState(false);
  const [showSamples, setShowSamples] = useState(false);
  const [showOutline, setShowOutline] = useState(false);
  // L1 inspector — closed by default; per-pane visibility persists.
  const [showInspector, setShowInspector] = useState(false);
  const [sparqlPaneOpen, setSparqlPaneOpen] = useState(() =>
    loadBool("qi_sparql_open", true),
  );
  const [aqlPaneOpen, setAqlPaneOpen] = useState(() =>
    loadBool("qi_aql_open", true),
  );
  const [autoOpenOnError, setAutoOpenOnError] = useState(() =>
    loadBool("qi_auto_open_error", true),
  );
  const [nlInput, setNlInput] = useState("");
  const [nlSamples, setNlSamples] = useState<string[]>([]);
  const [graphs, setGraphs] = useState<GraphInfo[]>([]);
  const [graphScope, setGraphScope] = useState<string | null>(null);
  const [graphBusy, setGraphBusy] = useState(false);
  const [graphError, setGraphError] = useState<string | null>(null);
  const sparqlViewRef = useRef<EditorView | null>(null);

  useEffect(() => {
    localStorage.setItem("qi_sparql_open", String(sparqlPaneOpen));
  }, [sparqlPaneOpen]);
  useEffect(() => {
    localStorage.setItem("qi_aql_open", String(aqlPaneOpen));
  }, [aqlPaneOpen]);
  useEffect(() => {
    localStorage.setItem("qi_auto_open_error", String(autoOpenOnError));
  }, [autoOpenOnError]);

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

  // Natural-language generate step. One /nl-translate call returns BOTH
  // the SPARQL (shown in the editor) and the ready-to-run AQL preview.
  // Returns the generated SPARQL on success (and updates `sparqlRef`
  // synchronously so a chained Send → Run reads the fresh query), or
  // null on empty input / failure.
  const runNL = useCallback(
    async (question: string): Promise<string | null> => {
      const q = question.trim();
      if (!q) return null;
      dispatch({ type: "NL_START", question: q });
      try {
        const resp = await nl2Sparql(q, {
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
        // Keep the ref in lock-step with the reducer so a chained
        // execute() in handleSend sees the just-generated query.
        sparqlRef.current = resp.sparql;
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
        return resp.sparql;
      } catch (err) {
        dispatch({
          type: "NL_ERROR",
          error: err instanceof Error ? err.message : String(err),
        });
        handleMaybeAuthError(err);
        return null;
      }
    },
    [dispatch, handleMaybeAuthError],
  );

  // The Send pipeline (PRD §10.0): always generate + transpile; run only
  // when connected. `planSend` encodes the degradation contract.
  const handleSend = useCallback(async () => {
    const question = nlInput.trim();
    if (!question) return;
    const intent = planSend(state.connection.status === "connected");
    const generated = await runNL(question);
    if (generated && generated.trim() && intent.run) {
      await handleExecute();
    }
  }, [nlInput, state.connection.status, runNL, handleExecute]);

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
  const pipelineFlags = {
    nlLoading: state.generating,
    translating: state.translating,
    executing: state.executing,
  };
  const busy = pipelineBusy(pipelineFlags);
  const stage = currentStage(pipelineFlags);

  // Reveal the inspector when a query fails so the user can see/fix the
  // offending SPARQL or AQL (toggleable via the gear, default on).
  useEffect(() => {
    if (autoOpenOnError && (state.error || state.nlError)) {
      setShowInspector(true);
    }
  }, [autoOpenOnError, state.error, state.nlError]);

  // At least one editor pane must stay open while the inspector is shown.
  const toggleSparqlPane = useCallback(() => {
    setSparqlPaneOpen((v) => (v && !aqlPaneOpen ? v : !v));
  }, [aqlPaneOpen]);
  const toggleAqlPane = useCallback(() => {
    setAqlPaneOpen((v) => (v && !sparqlPaneOpen ? v : !v));
  }, [sparqlPaneOpen]);

  // Ask-bar suggestions: recent questions first, then schema-derived
  // examples (POST /nl-samples), de-duplicated case-insensitively.
  const nlSuggestions = (() => {
    const seen = new Set<string>();
    const merged: string[] = [];
    for (const text of [...state.nlHistory, ...nlSamples]) {
      const key = text.trim().toLowerCase();
      if (key && !seen.has(key)) {
        seen.add(key);
        merged.push(text);
      }
    }
    return merged.slice(0, 10);
  })();

  const sparqlPane = (
    <div className="flex flex-col h-full min-h-0">
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
            <ClauseOutline sparql={state.sparql} onJumpToLine={handleJumpToLine} />
          </div>
        )}
      </div>
      <ParameterPanel
        sparql={state.sparql}
        params={state.params}
        onChange={(p) => dispatch({ type: "SET_PARAMS", params: p })}
      />
    </div>
  );

  const aqlPane = (
    <div className="flex flex-col h-full min-h-0">
      <div className="px-3 py-1.5 bg-gray-900/30 border-b border-gray-800 flex items-center gap-2">
        <span className="text-xs font-medium text-gray-400 uppercase tracking-wide">
          AQL
        </span>
        <div className="flex-1" />
        {state.translateMs != null && (
          <span className="text-[10px] text-emerald-500/70 tabular-nums">
            SPARQL&rarr;AQL {state.translateMs}ms
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
        <AqlEditor value={state.aql} bindVars={state.bindVars} error={null} />
      </div>
    </div>
  );

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
          <SettingsMenu
            showMapping={showMapping}
            onToggleMapping={() => setShowMapping((v) => !v)}
            showOutline={showOutline}
            onToggleOutline={() => setShowOutline((v) => !v)}
            onOpenSamples={() => setShowSamples(true)}
            onOpenHistory={() => setShowHistory(true)}
            historyCount={state.history.length}
            autoOpenOnError={autoOpenOnError}
            onToggleAutoOpenOnError={() => setAutoOpenOnError((v) => !v)}
          />
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

      {/* Schema-acquisition warnings (PRD §10.17). Sits above the main
          surface so analyzer-not-installed and W_SCHEMA_* messages are
          visible without scrolling. */}
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
        {showMapping && (
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
        )}

        <div className="flex-1 min-w-0 flex flex-col">
          {/* L0 — chat composer (NL "Ask") */}
          <ChatComposer
            value={nlInput}
            onChange={setNlInput}
            onSend={handleSend}
            busy={busy}
            suggestions={nlSuggestions}
            onPickSuggestion={setNlInput}
            contextSlot={
              <>
                {graphScope && (
                  <span
                    className="inline-flex items-center gap-1 px-1.5 py-0.5 text-[10px] rounded bg-indigo-600/20 text-indigo-300 border border-indigo-600/30"
                    title="Named-graph scope in effect"
                  >
                    graph: {graphScope}
                  </span>
                )}
                {!isConnected && (
                  <span
                    className="inline-flex items-center gap-1 px-1.5 py-0.5 text-[10px] rounded bg-amber-900/30 text-amber-400 border border-amber-800/40"
                    title="Send will generate SPARQL + AQL; connect to also run it"
                  >
                    not connected &middot; Send generates only
                  </span>
                )}
              </>
            }
            statusSlot={
              stage !== "idle" ? (
                <span
                  role="status"
                  className="text-[10px] text-violet-300/80 tabular-nums"
                >
                  {stageLabel(stage)}
                </span>
              ) : state.nlInfo && state.sparqlSource === "nl_pipeline" ? (
                <span
                  className="text-[10px] text-emerald-500/70 tabular-nums"
                  title="Generated from your natural-language question"
                >
                  from NL &middot; {state.nlInfo.latencyMs}ms &middot;{" "}
                  {state.nlInfo.llmCalls} call
                  {state.nlInfo.llmCalls === 1 ? "" : "s"}
                  {state.nlInfo.repaired ? " \u00b7 repaired" : ""}
                </span>
              ) : null
            }
          />

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

          {/* L0 — results are the primary surface and fill the space */}
          <div className="flex-1 min-h-0">
            <ResultsPanel
              results={state.results}
              warnings={state.warnings}
              activeTab={state.activeResultTab}
              dispatch={dispatch}
              execMs={state.execMs}
            />
          </div>

          {/* L1 — collapsible query inspector (SPARQL | AQL) */}
          <QueryInspector
            open={showInspector}
            onToggle={() => setShowInspector((v) => !v)}
            onTranslate={handleTranslate}
            onRun={handleExecute}
            translating={state.translating}
            executing={state.executing}
            busy={busy}
            isConnected={isConnected}
            sparqlEmpty={!state.sparql.trim()}
            sparqlOpen={sparqlPaneOpen}
            aqlOpen={aqlPaneOpen}
            onToggleSparql={toggleSparqlPane}
            onToggleAql={toggleAqlPane}
            sparqlPane={sparqlPane}
            aqlPane={aqlPane}
          />
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
