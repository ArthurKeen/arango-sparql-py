// Theme mode logic (WP-UI-THEME, PRD §10.8). Pure + DOM-free so the
// resolve/cycle rules are unit-testable; the React `useTheme` hook wraps
// these to apply the resolved theme to `document.documentElement` and
// persist the user's choice.

export type ThemeMode = "dark" | "light" | "system";
export type ResolvedTheme = "dark" | "light";

export const THEME_STORAGE_KEY = "sparql_theme";
export const THEME_MODES: ThemeMode[] = ["system", "dark", "light"];

/** Coerce arbitrary persisted input into a valid mode (defaults to system). */
export function parseThemeMode(raw: string | null | undefined): ThemeMode {
  return raw === "dark" || raw === "light" || raw === "system" ? raw : "system";
}

/** Resolve a mode to a concrete theme given the OS `prefers-color-scheme`. */
export function resolveTheme(mode: ThemeMode, prefersDark: boolean): ResolvedTheme {
  if (mode === "system") return prefersDark ? "dark" : "light";
  return mode;
}

/** Cycle system → dark → light → system for the toggle control. */
export function nextMode(mode: ThemeMode): ThemeMode {
  const i = THEME_MODES.indexOf(mode);
  return THEME_MODES[(i + 1) % THEME_MODES.length];
}

/** Short label for the toggle button. */
export function themeModeLabel(mode: ThemeMode): string {
  switch (mode) {
    case "dark":
      return "Dark";
    case "light":
      return "Light";
    default:
      return "System";
  }
}
