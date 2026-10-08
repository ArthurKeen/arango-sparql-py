import { describe, expect, it } from "vitest";
import { apiBaseFor } from "./client";

// Ported from arango-cypher-py's ui/src/api/client.test.ts. Regression guard
// for the deployed Workbench: under the Container Manager mount the old
// prefix-strip returned "" and sent /connect/platform to the cluster root,
// so the UI never auto-connected.
describe("apiBaseFor", () => {
  it("uses the platform mount when the SPA is served at the service root", () => {
    expect(apiBaseFor("/_service/uds/_db/IAM/arango-sparql-py/")).toBe(
      "/_service/uds/_db/IAM/arango-sparql-py",
    );
  });

  it("treats a mount opened without its trailing slash as a directory", () => {
    expect(apiBaseFor("/_service/uds/_db/IAM/arango-sparql-py")).toBe(
      "/_service/uds/_db/IAM/arango-sparql-py",
    );
  });

  it("drops an index.html page from the path", () => {
    expect(apiBaseFor("/_service/uds/_db/IAM/arango-sparql-py/index.html")).toBe(
      "/_service/uds/_db/IAM/arango-sparql-py",
    );
  });

  it("strips the /frontend and /ui sub-mounts to reach the API", () => {
    expect(apiBaseFor("/_service/uds/_db/d/app/frontend/")).toBe("/_service/uds/_db/d/app");
    expect(apiBaseFor("/_service/uds/_global/app/ui/index.html")).toBe("/_service/uds/_global/app");
    expect(apiBaseFor("/frontend/")).toBe("");
    expect(apiBaseFor("/ui")).toBe("");
  });

  it("is empty for local dev at the origin root", () => {
    expect(apiBaseFor("/")).toBe("");
    expect(apiBaseFor("")).toBe("");
  });

  it("compares whole segments, not substrings", () => {
    expect(apiBaseFor("/_service/uds/_db/d/ui-demo/")).toBe("/_service/uds/_db/d/ui-demo");
    expect(apiBaseFor("/_service/uds/_db/frontend-db/app/")).toBe("/_service/uds/_db/frontend-db/app");
  });
});

// ---------------------------------------------------------------------------
// schemaIntrospectUntilReady — catalog model (service/schema_warm.py)
// ---------------------------------------------------------------------------

import { afterEach, vi } from "vitest";
import {
  cancelSchemaPolls,
  schemaIntrospectUntilReady,
  SchemaPollSuperseded,
  type SchemaIntrospectResponse,
} from "./client";

function introspectBody(over: Partial<SchemaIntrospectResponse>): SchemaIntrospectResponse {
  return {
    mapping: {},
    summary: {},
    warnings: [],
    source: null,
    cache_hit: false,
    elapsed_ms: 0,
    status: "ready",
    warming: false,
    ...over,
  };
}

// Real fetch Responses, served in order; records each request URL.
function serve(bodies: SchemaIntrospectResponse[]): string[] {
  const urls: string[] = [];
  let i = 0;
  // apiBase() reads the page path; the vitest env is Node (no DOM).
  vi.stubGlobal("window", { location: { pathname: "/" } });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      urls.push(url);
      const body = bodies[Math.min(i++, bodies.length - 1)];
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
  return urls;
}

const noSleep = () => Promise.resolve();

describe("schemaIntrospectUntilReady", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("waits out 'pending' and returns the ready schema", async () => {
    const urls = serve([
      introspectBody({ status: "pending", warming: true }),
      introspectBody({ status: "pending", warming: true }),
      introspectBody({ status: "ready", mapping: { entities: {} } }),
    ]);
    const seen: string[] = [];
    const resp = await schemaIntrospectUntilReady(
      "tok",
      { include_owl: true },
      { sleep: noSleep, onAnalyzing: (r) => seen.push(String(r.status)) },
    );
    expect(resp.status).toBe("ready");
    expect(urls).toHaveLength(3);
    expect(seen).toEqual(["pending", "pending"]);
  });

  it("returns at once when the schema is already ready", async () => {
    const urls = serve([introspectBody({ status: "ready", cache_hit: true })]);
    const resp = await schemaIntrospectUntilReady("tok", {}, { sleep: noSleep });
    expect(resp.cache_hit).toBe(true);
    expect(urls).toHaveLength(1);
  });

  it("after a forced refresh, waits until warming ends — and never re-forces", async () => {
    const urls = serve([
      introspectBody({ status: "ready", warming: true }),
      introspectBody({ status: "ready", warming: true }),
      introspectBody({ status: "ready", warming: false }),
    ]);
    const resp = await schemaIntrospectUntilReady("tok", { force: true }, { sleep: noSleep });
    expect(resp.warming).toBe(false);
    expect(urls[0]).toContain("force=true");
    expect(urls.slice(1).every((u) => u.includes("force=false"))).toBe(true);
  });

  it("does not wait on warming for an unforced read (a stale schema is served)", async () => {
    const urls = serve([introspectBody({ status: "ready", warming: true })]);
    await schemaIntrospectUntilReady("tok", {}, { sleep: noSleep });
    expect(urls).toHaveLength(1);
  });

  it("gives up after maxWaitMs and returns the last pending answer", async () => {
    serve([introspectBody({ status: "pending", warming: true })]);
    let t = 0;
    const resp = await schemaIntrospectUntilReady(
      "tok",
      {},
      { sleep: async () => void (t += 60_000), now: () => t, maxWaitMs: 120_000 },
    );
    expect(resp.status).toBe("pending");
  });

  it("is superseded by a newer poll or a cancel, never delivering stale state", async () => {
    serve([introspectBody({ status: "pending", warming: true })]);
    const older = schemaIntrospectUntilReady("old-token", {}, {
      sleep: async () => {
        cancelSchemaPolls();
      },
    });
    await expect(older).rejects.toBeInstanceOf(SchemaPollSuperseded);
  });
});
