import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
    rules: {
      // The mutable-callback-ref pattern (`ref.current = currentValue`
      // assigned during render) is the documented React idiom for
      // capturing the latest event-handler closure inside a stable
      // long-lived listener. eslint-plugin-react-hooks v7 began
      // flagging it as an error; downgrade to a warning so we keep
      // the lint visible without breaking CI on every render-time
      // ref assignment in CodeMirror/Cytoscape integration code.
      // Same posture as `references/arango-cypher-py/ui`.
      'react-hooks/refs': 'warn',
      // setState inside an effect is also flagged by v7 even when
      // it's the canonical "subscribe → setState on first event"
      // pattern. Warn-level keeps it surfaced without forcing a
      // refactor of every controlled-input mirror effect.
      'react-hooks/set-state-in-effect': 'warn',
      // The ResultsPanel/CytoscapeGraph modules export a small set of
      // pure helpers alongside the default React component so unit
      // tests can exercise the triple-extraction logic without a
      // DOM. React Fast Refresh only-exports-components is a Vite
      // HMR optimisation, not a correctness rule — warn-level keeps
      // the hint without blocking CI.
      'react-refresh/only-export-components': 'warn',
    },
  },
])
