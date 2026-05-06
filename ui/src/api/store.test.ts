/**
 * Reducer state-machine tests for the SPARQL UI store.
 *
 * Mirrors `references/arango-cypher-py/ui/src/api/store.test.ts` —
 * pure-reducer tests, no React, no DOM. Vitest runs them under
 * `npm run test`.
 */
import { describe, expect, it } from "vitest";

import {
  type Action,
  type AppState,
  initialState,
} from "./store";
import { __reducerForTest as reducer } from "./store";

function apply(state: AppState, ...actions: Action[]): AppState {
  return actions.reduce(reducer, state);
}

describe("reducer: SPARQL editor state", () => {
  it("SET_SPARQL replaces the editor contents", () => {
    const next = apply(initialState, {
      type: "SET_SPARQL",
      sparql: "SELECT * WHERE { ?s ?p ?o } LIMIT 10",
    });
    expect(next.sparql).toBe("SELECT * WHERE { ?s ?p ?o } LIMIT 10");
  });

  it("SET_ONTOLOGY_TTL replaces the Turtle text", () => {
    const ttl = "@prefix ex: <http://example.org/> .\nex:Person a owl:Class .";
    const next = apply(initialState, { type: "SET_ONTOLOGY_TTL", ontologyTtl: ttl });
    expect(next.ontologyTtl).toBe(ttl);
  });
});

describe("reducer: connection lifecycle", () => {
  it("CONNECT_START flips status to connecting and pins url/db/user", () => {
    const next = apply(initialState, {
      type: "CONNECT_START",
      url: "http://localhost:8529",
      database: "my_db",
      username: "alice",
    });
    expect(next.connection.status).toBe("connecting");
    expect(next.connection.database).toBe("my_db");
    expect(next.connection.username).toBe("alice");
    expect(next.connection.error).toBeNull();
  });

  it("CONNECT_SUCCESS records the session token and database list", () => {
    const next = apply(
      initialState,
      {
        type: "CONNECT_START",
        url: "http://localhost:8529",
        database: "_system",
        username: "root",
      },
      {
        type: "CONNECT_SUCCESS",
        token: "tok-abc",
        databases: ["_system", "my_db"],
        url: "http://localhost:8529",
        database: "_system",
        username: "root",
        password: "p",
      },
    );
    expect(next.connection.status).toBe("connected");
    expect(next.connection.token).toBe("tok-abc");
    expect(next.connection.databases).toEqual(["_system", "my_db"]);
  });

  it("DISCONNECT clears token, databases, and any cached results", () => {
    const connected = apply(
      initialState,
      {
        type: "CONNECT_SUCCESS",
        token: "tok",
        databases: ["_system"],
        url: "http://localhost:8529",
        database: "_system",
        username: "root",
        password: "p",
      },
      { type: "EXECUTE_SUCCESS", results: [{ x: 1 }] },
    );
    expect(connected.results).not.toBeNull();
    const next = apply(connected, { type: "DISCONNECT" });
    expect(next.connection.status).toBe("disconnected");
    expect(next.connection.token).toBeNull();
    expect(next.connection.databases).toEqual([]);
    expect(next.results).toBeNull();
  });
});

describe("reducer: translate / execute lifecycle", () => {
  it("TRANSLATE_SUCCESS preserves a previously measured translateMs", () => {
    const after = apply(
      initialState,
      { type: "TRANSLATE_START" },
      {
        type: "TRANSLATE_SUCCESS",
        aql: "FOR x IN c RETURN x",
        bindVars: {},
        translateMs: 12,
      },
      // A second TRANSLATE_SUCCESS without an explicit translateMs
      // should NOT clobber the previous value.
      {
        type: "TRANSLATE_SUCCESS",
        aql: "FOR x IN c RETURN x",
        bindVars: { y: 1 },
      },
    );
    expect(after.translateMs).toBe(12);
    expect(after.bindVars).toEqual({ y: 1 });
  });

  it("EXECUTE_SUCCESS sets results, exec time, and switches to table tab", () => {
    const next = apply(
      initialState,
      { type: "EXECUTE_START" },
      {
        type: "EXECUTE_SUCCESS",
        results: [{ s: "ex:a", p: "ex:knows", o: "ex:b" }],
        execMs: 7,
      },
    );
    expect(next.results).toHaveLength(1);
    expect(next.execMs).toBe(7);
    expect(next.activeResultTab).toBe("table");
  });

  it("TRANSLATE_ERROR records the error and clears the translating flag", () => {
    const next = apply(
      initialState,
      { type: "TRANSLATE_START" },
      { type: "TRANSLATE_ERROR", error: "Unsupported SPARQL: SERVICE" },
    );
    expect(next.translating).toBe(false);
    expect(next.error).toBe("Unsupported SPARQL: SERVICE");
  });
});

describe("reducer: history dedup", () => {
  it("ADD_HISTORY moves an existing entry to the top instead of duplicating", () => {
    const first = apply(initialState, {
      type: "ADD_HISTORY",
      entry: { sparql: "SELECT * WHERE {}", timestamp: 1, aqlPreview: "FOR x" },
    });
    const second = apply(first, {
      type: "ADD_HISTORY",
      entry: { sparql: "SELECT * WHERE {}", timestamp: 2, aqlPreview: "FOR y" },
    });
    expect(second.history).toHaveLength(1);
    expect(second.history[0].timestamp).toBe(2);
  });
});
