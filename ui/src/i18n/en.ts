// English message catalogue (WP-UI-A11Y, PRD §10.10 i18n row).
//
// No translations ship in v1.0 — this file + the `t()` indirection exist
// so that when translations DO land, component code needs no refactor.
// New user-visible strings should be added here and referenced via
// `t("some.key")` rather than hardcoded in JSX. Migration of existing
// strings is incremental; `scripts/check-i18n.mjs` reports remaining
// hardcoded JSX text as an advisory.
//
// Interpolation uses `{name}` placeholders resolved by `t(key, vars)`.

export const messages = {
  "app.title": "Arango SPARQL",

  "action.translate": "Translate",
  "action.run": "Run",
  "action.explain": "Explain",
  "action.profile": "Profile",
  "action.runAql": "Run AQL",
  "action.close": "Close",
  "action.clearAll": "Clear All",
  "action.reset": "Reset",

  "aria.close": "Close",
  "aria.settings": "Settings",
  "aria.send": "Send",
  "aria.commandPalette": "Command palette",
  "aria.status": "Status",

  "theme.toNight": "Switch to night mode",
  "theme.toDay": "Switch to day mode",

  "composer.placeholder": "Ask a question about your data\u2026",
  "schema.loading": "Loading schema\u2026",
  "schema.analyzing": "Analyzing schema\u2026 (the first analysis of a large database can take several minutes)",
  "schema.stillAnalyzing": "The schema is still being analyzed in the background \u2014 it will load on the next schema refresh.",
  "schema.pendingGraph": "This database's schema is being analyzed in the background\u2026",
  "status.notConnected": "Not connected \u2014 Send generates & transpiles only",

  "status.idle": "Ready",
  "status.translating": "Translating…",
  "status.executing": "Running…",
  "status.explaining": "Explaining…",
  "status.profiling": "Profiling…",
  "status.thinking": "Thinking…",
  "status.rows": "{count} rows",
  "status.error": "Error: {message}",

  "panel.sparql": "SPARQL",
  "panel.aql": "AQL",
  "panel.results": "Results",
  "panel.history": "Query History",
  "panel.samples": "Sample SPARQL Queries",

  "empty.noSchema": "No schema loaded",
} as const;

export type MessageKey = keyof typeof messages;
