// Translation lookup (WP-UI-A11Y). Pure + synchronous — the active locale
// is English-only for v1.0, so `t()` reads the `en` catalogue directly.
// When more locales land, swap the module-level `catalogue` for a
// locale-aware selector; call sites (`t("key", vars)`) stay unchanged.

import { messages, type MessageKey } from "./en";

const catalogue: Record<string, string> = messages;

/**
 * Resolve a message key to its string, interpolating `{name}` placeholders
 * from `vars`. Unknown keys fall back to the key itself so a missing
 * translation degrades to a visible (if ugly) marker rather than blank.
 */
export function t(
  key: MessageKey,
  vars?: Record<string, string | number>,
): string {
  let out = catalogue[key] ?? key;
  if (vars) {
    for (const [name, value] of Object.entries(vars)) {
      out = out.split(`{${name}}`).join(String(value));
    }
  }
  return out;
}

export type { MessageKey };
