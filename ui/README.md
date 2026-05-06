# `ui/` — Vite + React + TypeScript frontend

Mirrors `references/arango-cypher-py/ui/`. Bootstrap with:

```bash
cd ui
npm install
npm run dev
```

Component map (1:1 with the Cypher UI where it carries over) lives in
`.cursor/rules/400-frontend-ui.mdc`. The key UX delta vs Cypher: render
RDF literal nodes as collapsed properties on their subject node by
default, with a toggle to opt back into raw triple view.
