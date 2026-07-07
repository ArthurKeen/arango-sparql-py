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

  "theme.label": "Theme",
  "theme.system": "System",
  "theme.dark": "Dark",
  "theme.light": "Light",

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
