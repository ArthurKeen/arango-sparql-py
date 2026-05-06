import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// `arango_sparql.service` runs on :8001 by default (mirrors the Cypher
// service). Every endpoint the UI knows how to talk to is proxied so a
// dev-mode SPA never needs CORS. Endpoints that the SPARQL backend does
// not implement yet (e.g. `/explain`, `/nl2sparql`) are still proxied so
// the wire failures surface at the service tier with the right status
// code instead of a stray 404 from the dev server.
export default defineConfig({
  base: "./",
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      "/connect": "http://localhost:8001",
      "/disconnect": "http://localhost:8001",
      "/connections": "http://localhost:8001",
      "/translate": "http://localhost:8001",
      "/execute": "http://localhost:8001",
      "/execute-aql": "http://localhost:8001",
      "/validate": "http://localhost:8001",
      "/explain": "http://localhost:8001",
      "/aql-profile": "http://localhost:8001",
      "/sparql-profile": "http://localhost:8001",
      "/nl2sparql": "http://localhost:8001",
      "/nl2aql": "http://localhost:8001",
      "/nl-samples": "http://localhost:8001",
      "/sample-queries": "http://localhost:8001",
      "/schema": "http://localhost:8001",
      "/health": "http://localhost:8001",
    },
  },
  build: {
    outDir: "dist",
  },
});
