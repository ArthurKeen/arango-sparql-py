import { useCallback, useReducer } from "react";

// `arango-sparql-py` UI store. Mirrors
// `references/arango-cypher-py/ui/src/api/store.ts` but trimmed to the
// subset of state the SPARQL backend currently supports:
//
//   * `sparql` (the editor's query text) instead of `cypher`
//   * `ontologyTtl` (raw Turtle text passed to /translate as
//     `ontology_ttl`) instead of `mapping` (the Cypher schema mapping
//     JSON object).
//
// The reducer surface intentionally keeps SET_SPARQL/SET_ONTOLOGY/...
// names symmetrical with the Cypher store so a developer who knows
// one repo reads the other in seconds.

export interface ConnectionState {
  status: "disconnected" | "connecting" | "connected";
  token: string | null;
  url: string;
  database: string;
  username: string;
  password: string;
  databases: string[];
  error: string | null;
}

export type ResultTab = "table" | "json" | "graph";

export interface HistoryEntry {
  sparql: string;
  timestamp: number;
  aqlPreview: string;
}

// Schema-acquisition surface (PRD §6.4 — backed by
// `arango_sparql.schema.acquire`). Populated by ConnectionDialog
// after a successful /connect call (auto-introspect) and by the
// MappingPanel's Import / refresh affordances.
export interface SchemaWarning {
  code: string;
  message: string;
  install_hint?: string;
}

export type SchemaCacheStatus =
  | "idle"
  | "no_cache"
  | "unchanged"
  | "stats_only"
  | "shape_changed";

export interface SchemaState {
  /** Canonical mapping wire dict from /schema/introspect. */
  mapping: Record<string, unknown> | null;
  /** Server-side summary block (entity count, RPT collections, …). */
  summary: Record<string, unknown> | null;
  /** Schema warnings (e.g. ANALYZER_NOT_INSTALLED, W_SCHEMA_*). */
  warnings: SchemaWarning[];
  /** Drift status from the most recent /schema/status call. */
  cacheStatus: SchemaCacheStatus;
  /** True when the latest introspect was served from L1 cache. */
  cacheHit: boolean;
  /** Last successful introspect timestamp (Date.now()). */
  lastFetchedAt: number | null;
  /** True while a /schema/{introspect,force-reacquire} call is in flight. */
  refreshing: boolean;
  /** Last error from a schema-API call, if any. Cleared on success. */
  error: string | null;
  /** Triple count reported by the most recent OWL import (UI badge). */
  lastImportTripleCount: number | null;
}

export const initialSchemaState: SchemaState = {
  mapping: null,
  summary: null,
  warnings: [],
  cacheStatus: "idle",
  cacheHit: false,
  lastFetchedAt: null,
  refreshing: false,
  error: null,
  lastImportTripleCount: null,
};

export interface AppState {
  connection: ConnectionState;
  sparql: string;
  // Raw Turtle/OWL ontology text. Sent to /translate as
  // `ontology_ttl` (see `arango_sparql.service.models`). Stored as a
  // string rather than a parsed graph because the backend re-parses
  // it anyway and round-tripping JSON would lose whitespace /
  // comments the user sees in the editor.
  ontologyTtl: string;
  params: Record<string, unknown>;
  aql: string;
  bindVars: Record<string, unknown>;
  results: unknown[] | null;
  warnings: Array<{ message: string }>;
  activeResultTab: ResultTab;
  error: string | null;
  translating: boolean;
  executing: boolean;
  history: HistoryEntry[];
  translateMs: number | null;
  execMs: number | null;
  /** Schema-acquisition slice (PRD §6.4 wire shapes). */
  schema: SchemaState;
}

const STORAGE_KEY = "sparql-workbench";

const MAX_HISTORY = 50;

function loadSavedState(): Partial<AppState> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const saved = JSON.parse(raw);
    return {
      sparql: saved.sparql ?? "",
      ontologyTtl: saved.ontologyTtl ?? "",
      params: saved.params ?? {},
      history: Array.isArray(saved.history) ? saved.history.slice(0, MAX_HISTORY) : [],
    };
  } catch {
    return {};
  }
}

function saveState(state: AppState) {
  try {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        sparql: state.sparql,
        ontologyTtl: state.ontologyTtl,
        params: state.params,
        history: state.history.slice(0, MAX_HISTORY),
      }),
    );
  } catch {
    // localStorage may be unavailable
  }
}

const DEFAULT_SPARQL = `PREFIX rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX ex:   <http://example.org/>

SELECT ?person ?name
WHERE {
  ?person a ex:Person ;
          ex:name ?name .
}
LIMIT 100
`;

export const initialState: AppState = {
  connection: {
    status: "disconnected",
    token: null,
    url: "http://localhost:8529",
    database: "_system",
    username: "root",
    password: "",
    databases: [],
    error: null,
  },
  sparql: DEFAULT_SPARQL,
  ontologyTtl: "",
  params: {},
  aql: "",
  bindVars: {},
  results: null,
  warnings: [],
  activeResultTab: "table",
  error: null,
  translating: false,
  executing: false,
  history: [],
  translateMs: null,
  execMs: null,
  schema: initialSchemaState,
  ...loadSavedState(),
};

export type Action =
  | { type: "SET_SPARQL"; sparql: string }
  | { type: "SET_ONTOLOGY_TTL"; ontologyTtl: string }
  | {
      type: "CONNECT_START";
      url: string;
      database: string;
      username: string;
    }
  | {
      type: "CONNECT_SUCCESS";
      token: string;
      databases: string[];
      url: string;
      database: string;
      username: string;
      password: string;
    }
  | { type: "CONNECT_ERROR"; error: string }
  | { type: "DISCONNECT" }
  | { type: "TRANSLATE_START" }
  | {
      type: "TRANSLATE_SUCCESS";
      aql: string;
      bindVars: Record<string, unknown>;
      warnings?: Array<{ message: string }>;
      translateMs?: number | null;
    }
  | { type: "TRANSLATE_ERROR"; error: string }
  | { type: "EXECUTE_START" }
  | { type: "EXECUTE_SUCCESS"; results: unknown[]; warnings?: Array<{ message: string }>; execMs?: number | null }
  | { type: "EXECUTE_ERROR"; error: string }
  | { type: "SET_RESULT_TAB"; tab: ResultTab }
  | { type: "CLEAR_ERROR" }
  | { type: "SET_PARAMS"; params: Record<string, unknown> }
  | { type: "ADD_HISTORY"; entry: HistoryEntry }
  | { type: "CLEAR_HISTORY" }
  | { type: "SCHEMA_REFRESH_START" }
  | {
      type: "SCHEMA_LOADED";
      mapping: Record<string, unknown> | null;
      summary: Record<string, unknown> | null;
      warnings: SchemaWarning[];
      cacheHit: boolean;
    }
  | { type: "SCHEMA_REFRESH_ERROR"; error: string }
  | { type: "SCHEMA_STATUS_UPDATED"; status: SchemaCacheStatus }
  | { type: "SCHEMA_CACHE_INVALIDATED" }
  | { type: "SCHEMA_IMPORT_SUCCESS"; tripleCount: number };

function reducer(state: AppState, action: Action): AppState {
  switch (action.type) {
    case "SET_SPARQL":
      return { ...state, sparql: action.sparql };
    case "SET_ONTOLOGY_TTL":
      return { ...state, ontologyTtl: action.ontologyTtl };
    case "CONNECT_START":
      return {
        ...state,
        connection: {
          ...state.connection,
          status: "connecting",
          url: action.url,
          database: action.database,
          username: action.username,
          error: null,
        },
      };
    case "CONNECT_SUCCESS":
      return {
        ...state,
        connection: {
          status: "connected",
          token: action.token,
          url: action.url,
          database: action.database,
          username: action.username,
          password: action.password,
          databases: action.databases,
          error: null,
        },
      };
    case "CONNECT_ERROR":
      return {
        ...state,
        connection: {
          ...state.connection,
          status: "disconnected",
          error: action.error,
        },
      };
    case "DISCONNECT":
      return {
        ...state,
        connection: {
          ...state.connection,
          status: "disconnected",
          token: null,
          databases: [],
          error: null,
        },
        results: null,
        // Clear the schema slice so a reconnect to a different DB
        // doesn't render the previous DB's mapping for one frame
        // before the new /schema/introspect lands.
        schema: initialSchemaState,
      };
    case "TRANSLATE_START":
      return { ...state, translating: true, error: null, translateMs: null };
    case "TRANSLATE_SUCCESS":
      return {
        ...state,
        translating: false,
        aql: action.aql,
        bindVars: action.bindVars,
        warnings: action.warnings ?? state.warnings,
        translateMs:
          action.translateMs !== undefined ? action.translateMs : state.translateMs,
        error: null,
      };
    case "TRANSLATE_ERROR":
      return { ...state, translating: false, error: action.error };
    case "EXECUTE_START":
      return { ...state, executing: true, error: null, execMs: null };
    case "EXECUTE_SUCCESS":
      return {
        ...state,
        executing: false,
        results: action.results,
        warnings: action.warnings ?? state.warnings,
        execMs: action.execMs ?? null,
        activeResultTab: "table",
        error: null,
      };
    case "EXECUTE_ERROR":
      return { ...state, executing: false, error: action.error };
    case "SET_RESULT_TAB":
      return { ...state, activeResultTab: action.tab };
    case "CLEAR_ERROR":
      return { ...state, error: null };
    case "SET_PARAMS":
      return { ...state, params: action.params };
    case "ADD_HISTORY": {
      const exists = state.history.some((h) => h.sparql === action.entry.sparql);
      const updated = exists
        ? [action.entry, ...state.history.filter((h) => h.sparql !== action.entry.sparql)]
        : [action.entry, ...state.history];
      return { ...state, history: updated.slice(0, MAX_HISTORY) };
    }
    case "CLEAR_HISTORY":
      return { ...state, history: [] };
    case "SCHEMA_REFRESH_START":
      return {
        ...state,
        schema: { ...state.schema, refreshing: true, error: null },
      };
    case "SCHEMA_LOADED":
      return {
        ...state,
        schema: {
          ...state.schema,
          mapping: action.mapping,
          summary: action.summary,
          warnings: action.warnings,
          cacheHit: action.cacheHit,
          // Drift status moves to "unchanged" on a fresh load — the
          // server-side cache and the just-loaded mapping share a
          // fingerprint by definition.
          cacheStatus: "unchanged",
          lastFetchedAt: Date.now(),
          refreshing: false,
          error: null,
        },
      };
    case "SCHEMA_REFRESH_ERROR":
      return {
        ...state,
        schema: {
          ...state.schema,
          refreshing: false,
          error: action.error,
        },
      };
    case "SCHEMA_STATUS_UPDATED":
      return {
        ...state,
        schema: { ...state.schema, cacheStatus: action.status },
      };
    case "SCHEMA_CACHE_INVALIDATED":
      return {
        ...state,
        schema: {
          ...state.schema,
          cacheStatus: "no_cache",
          cacheHit: false,
        },
      };
    case "SCHEMA_IMPORT_SUCCESS":
      return {
        ...state,
        schema: {
          ...state.schema,
          lastImportTripleCount: action.tripleCount,
        },
      };
    default:
      return state;
  }
}

// Re-exported pure reducer so unit tests can drive state transitions
// without a React tree. Mirrors the Cypher UI's `__reducerForTest`.
export { reducer as __reducerForTest };

export function useAppState() {
  const [state, dispatch] = useReducer(reducer, initialState);

  const PERSIST_ACTIONS = new Set([
    "SET_SPARQL",
    "SET_ONTOLOGY_TTL",
    "SET_PARAMS",
    "ADD_HISTORY",
    "CLEAR_HISTORY",
  ]);

  const persistAndDispatch = useCallback(
    (action: Action) => {
      dispatch(action);
      if (PERSIST_ACTIONS.has(action.type)) {
        const next = reducer(state, action);
        saveState(next);
      }
    },
    [state],
  );

  return [state, persistAndDispatch] as const;
}
