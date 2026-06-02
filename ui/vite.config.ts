import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// `arango_sparql.service` runs on :8001 by default (mirrors the Cypher
// service). Every endpoint the UI knows how to talk to is proxied so a
// dev-mode SPA never needs CORS. Endpoints that the SPARQL backend does
// not implement yet (e.g. `/explain`, `/nl2sparql`) are still proxied so
// the wire failures surface at the service tier with the right status
// code instead of a stray 404 from the dev server.
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
  "/aql-profile",
  "/sparql-profile",
  "/nl2sparql",
  "/nl2aql",
  "/nl-samples",
  "/sample-queries",
  "/schema",
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
