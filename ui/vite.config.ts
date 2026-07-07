import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// `arango_sparql.service` runs on :8001 by default (mirrors the Cypher
// service). Every endpoint the UI knows how to talk to is proxied so a
// dev-mode SPA never needs CORS. Each prefix below is matched against the
// real backend routes (NL pipeline: `/nl-translate`, `/nl-execute`,
// `/nl-explain`, `/nl-samples`; named-graph scoping: `/graphs`,
// `/session/graph`; OWL roundtrip: `/mapping/import-owl`,
// `/mapping/export-owl`; sample corpus: `/sample-queries`). Any request
// that isn't proxied falls through to the Vite dev server and 404s, so
// this list must track the service routes.
//
// The target is overridable via `SPARQL_API_TARGET` so the SPARQL
// service can run on an alternate port when the default :8001 is taken
// by the sibling Cypher service (e.g. `SPARQL_API_TARGET=http://localhost:8002`).
const apiTarget = process.env.SPARQL_API_TARGET ?? "http://localhost:8001";

const proxiedPaths = [
  "/connect",
  "/disconnect",
  "/connections",
  "/translate",
  "/execute",
  "/execute-aql",
  "/validate",
  "/explain",
  "/profile",
  "/nl-translate",
  "/nl-explain",
  "/nl-execute",
  "/nl-samples",
  "/graphs",
  "/session",
  "/schema",
  "/mapping",
  "/sample-queries",
  "/health",
];

export default defineConfig({
  base: "./",
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: Object.fromEntries(proxiedPaths.map((path) => [path, apiTarget])),
  },
  build: {
    outDir: "dist",
  },
});
