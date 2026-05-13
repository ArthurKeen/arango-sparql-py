import { useEffect, useRef, useState } from "react";
import {
  connect,
  disconnect,
  getConnectDefaults,
  schemaIntrospect,
  type ConnectDefaults,
  type SchemaIntrospectResponse,
} from "../api/client";
import type { Action, ConnectionState, SchemaWarning } from "../api/store";

// `ConnectionDialog` for the SPARQL UI. After a successful
// `/connect` it kicks off a `/schema/introspect` round-trip so the
// MappingPanel and AQL editor can render the analyzer-acquired
// MappingBundle without the user having to author / upload a
// Turtle ontology by hand. Mirrors the Cypher project's
// "auto-introspect after connect" flow (see PRD §6.4 and §10.2).
//
// The introspect call is best-effort: a 503 (analyzer not
// installed and heuristic disabled, per the §6.3.4 four-cell
// table) is reported via the schema warnings banner rather than
// failing the connection itself — a user with a SPARQL workload
// against a DB they cannot introspect can still author their
// own ontology in the MappingPanel and run queries.

interface Props {
  connection: ConnectionState;
  dispatch: (action: Action) => void;
  /** Optional callback so App.tsx can prefill the MappingPanel
   * editor with the OWL/Turtle that auto-introspect returns. We
   * accept it as a prop rather than dispatching SET_ONTOLOGY_TTL
   * directly so the dialog keeps its single-responsibility shape
   * — App.tsx owns the editor / Turtle coupling. */
  onSchemaLoaded?: (turtle: string | null) => void;
}

/**
 * Project the introspect response into the SchemaWarning shape the
 * store + banner expect. Tolerates partial / typed-but-loose
 * payloads so an analyzer-version mismatch doesn't crash the UI.
 */
function _normaliseWarnings(
  resp: SchemaIntrospectResponse | null,
): SchemaWarning[] {
  if (!resp || !Array.isArray(resp.warnings)) return [];
  return resp.warnings
    .filter((w): w is SchemaWarning =>
      typeof w === "object" && w !== null && typeof w.code === "string"
        ? typeof w.message === "string"
        : false,
    )
    .map((w) => ({
      code: w.code,
      message: w.message,
      install_hint: w.install_hint,
    }));
}

/**
 * Pull the inline OWL/Turtle out of an introspect response. The
 * mapping wire dict accepts both `owlTurtle` (canonical camelCase)
 * and `owl_turtle` (Python-side snake_case) so we check both.
 * Returns null when the analyzer didn't emit OWL.
 */
function _extractTurtle(resp: SchemaIntrospectResponse | null): string | null {
  if (!resp || !resp.mapping || typeof resp.mapping !== "object") return null;
  const mapping = resp.mapping as Record<string, unknown>;
  const owl =
    (mapping.owlTurtle as string | undefined) ??
    (mapping.owl_turtle as string | undefined);
  return typeof owl === "string" && owl.trim().length > 0 ? owl : null;
}

export default function ConnectionDialog({
  connection,
  dispatch,
  onSchemaLoaded,
}: Props) {
  const [form, setForm] = useState({
    url: connection.url,
    database: connection.database,
    username: connection.username,
    password: "",
  });
  const [open, setOpen] = useState(false);
  const autoConnectAttempted = useRef(false);

  useEffect(() => {
    if (connection.status === "disconnected") {
      setForm((f) => ({
        ...f,
        url: connection.url,
        database: connection.database,
        username: connection.username,
      }));
    }
  }, [connection.status, connection.url, connection.database, connection.username]);

  useEffect(() => {
    getConnectDefaults()
      .then((defaults: ConnectDefaults) => {
        const newForm = {
          url: defaults.url || form.url,
          database:
            form.database && form.database !== "_system"
              ? form.database
              : defaults.database || form.database,
          username: defaults.username || form.username,
          password: defaults.password || form.password,
        };
        setForm(newForm);

        if (
          !autoConnectAttempted.current &&
          defaults.password &&
          connection.status === "disconnected"
        ) {
          autoConnectAttempted.current = true;
          doConnect(newForm);
        }
      })
      .catch((err) => {
        // /connect/defaults may not be implemented on the SPARQL
        // backend yet (the endpoint is documented as future-work in
        // models.py). Surface in the console rather than silently
        // dropping — the form falls back to its hardcoded defaults.
        console.warn("Failed to load /connect/defaults:", err);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function doConnect(f: typeof form) {
    dispatch({
      type: "CONNECT_START",
      url: f.url,
      database: f.database,
      username: f.username,
    });
    try {
      const resp = await connect({
        url: f.url,
        database: f.database,
        username: f.username,
        password: f.password,
      });
      dispatch({
        type: "CONNECT_SUCCESS",
        token: resp.token,
        databases: resp.databases,
        url: f.url,
        database: f.database,
        username: f.username,
        password: f.password,
      });
      setOpen(false);
      // Fire-and-forget auto-introspect (PRD §6.4 + §10.2). Awaited
      // so a fast backend completes the dispatch before React commits
      // the post-connect render; error path logs and degrades to
      // "no schema loaded" rather than rolling back the connection.
      void doAutoIntrospect(resp.token, f.database);
    } catch (err) {
      dispatch({
        type: "CONNECT_ERROR",
        error: err instanceof Error ? err.message : String(err),
      });
    }
  }

  async function doAutoIntrospect(token: string, database: string) {
    dispatch({ type: "SCHEMA_REFRESH_START" });
    try {
      const resp = await schemaIntrospect(token, {
        database,
        include_owl: true,
        include_statistics: true,
      });
      dispatch({
        type: "SCHEMA_LOADED",
        mapping: resp.mapping ?? null,
        summary: resp.summary ?? null,
        warnings: _normaliseWarnings(resp),
        cacheHit: !!resp.cache_hit,
      });
      const turtle = _extractTurtle(resp);
      if (turtle && onSchemaLoaded) onSchemaLoaded(turtle);
    } catch (err) {
      // Non-fatal — the user can still author an ontology by hand.
      // We surface the error in the schema slice so the warning
      // banner can render it; the connection itself remains active.
      const message =
        err instanceof Error ? err.message : String(err);
      dispatch({ type: "SCHEMA_REFRESH_ERROR", error: message });
      console.warn("auto-introspect failed:", err);
    }
  }

  async function handleConnect() {
    await doConnect(form);
  }

  async function handleSwitchDb(newDb: string) {
    if (newDb === connection.database) return;

    if (connection.token) {
      try {
        await disconnect(connection.token);
      } catch {
        /* best-effort */
      }
    }

    const f = { ...form, database: newDb };
    setForm(f);
    await doConnect(f);
  }

  async function handleDisconnect() {
    if (connection.token) {
      try {
        await disconnect(connection.token);
      } catch {
        /* best-effort */
      }
    }
    dispatch({ type: "DISCONNECT" });
  }

  if (connection.status === "connected") {
    return (
      <div className="flex items-center gap-3 text-sm">
        <span className="flex items-center gap-1.5">
          <span className="inline-block w-2 h-2 rounded-full bg-emerald-400" />
          <span
            className="text-gray-400 text-xs truncate max-w-[200px]"
            title={connection.url}
          >
            {connection.url.replace(/^https?:\/\//, "")}/
          </span>
          {connection.databases.length > 1 ? (
            <select
              value={connection.database}
              onChange={(e) => handleSwitchDb(e.target.value)}
              className="bg-gray-800 border border-gray-600 text-gray-200 text-sm rounded px-1.5 py-0.5 focus:border-indigo-500 focus:outline-none cursor-pointer"
            >
              {connection.databases.map((db) => (
                <option key={db} value={db}>
                  {db}
                </option>
              ))}
            </select>
          ) : (
            <span className="text-gray-300">{connection.database}</span>
          )}
        </span>
        <button
          onClick={handleDisconnect}
          className="px-2.5 py-1 text-xs rounded bg-gray-700 hover:bg-gray-600 text-gray-300 transition-colors"
        >
          Disconnect
        </button>
      </div>
    );
  }

  if (!open && connection.status === "connecting") {
    return <span className="text-sm text-gray-400">Connecting...</span>;
  }

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="px-3 py-1.5 text-sm rounded bg-indigo-600 hover:bg-indigo-500 text-white transition-colors"
      >
        Connect to ArangoDB
      </button>
    );
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="bg-gray-800 rounded-lg shadow-2xl p-6 w-full max-w-md border border-gray-700">
        <h2 className="text-lg font-semibold mb-4 text-white">
          Connect to ArangoDB
        </h2>

        {connection.error && (
          <div className="mb-4 p-3 rounded bg-red-900/50 border border-red-700 text-red-300 text-sm">
            {connection.error}
          </div>
        )}

        <div className="space-y-3">
          <label>
            <span className="text-xs text-gray-400 block mb-1">URL</span>
            <input
              value={form.url}
              onChange={(e) => setForm({ ...form, url: e.target.value })}
              placeholder="http://localhost:8529 or https://cloud.arangodb.com"
              className="w-full px-3 py-2 rounded bg-gray-900 border border-gray-600 text-sm text-white focus:border-indigo-500 focus:outline-none"
            />
          </label>
          <label>
            <span className="text-xs text-gray-400 block mb-1">Database</span>
            <input
              value={form.database}
              onChange={(e) => setForm({ ...form, database: e.target.value })}
              className="w-full px-3 py-2 rounded bg-gray-900 border border-gray-600 text-sm text-white focus:border-indigo-500 focus:outline-none"
            />
          </label>
          <label>
            <span className="text-xs text-gray-400 block mb-1">Username</span>
            <input
              value={form.username}
              onChange={(e) => setForm({ ...form, username: e.target.value })}
              className="w-full px-3 py-2 rounded bg-gray-900 border border-gray-600 text-sm text-white focus:border-indigo-500 focus:outline-none"
            />
          </label>
          <label>
            <span className="text-xs text-gray-400 block mb-1">Password</span>
            <input
              type="password"
              value={form.password}
              onChange={(e) => setForm({ ...form, password: e.target.value })}
              onKeyDown={(e) => e.key === "Enter" && handleConnect()}
              className="w-full px-3 py-2 rounded bg-gray-900 border border-gray-600 text-sm text-white focus:border-indigo-500 focus:outline-none"
            />
          </label>
        </div>

        <div className="flex justify-end gap-3 mt-6">
          <button
            onClick={() => setOpen(false)}
            className="px-4 py-2 text-sm rounded bg-gray-700 hover:bg-gray-600 text-gray-300 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleConnect}
            disabled={connection.status === "connecting"}
            className="px-4 py-2 text-sm rounded bg-indigo-600 hover:bg-indigo-500 text-white transition-colors disabled:opacity-50"
          >
            {connection.status === "connecting" ? "Connecting..." : "Connect"}
          </button>
        </div>
      </div>
    </div>
  );
}
