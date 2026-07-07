import { useCallback, useEffect, useState } from "react";
import {
  THEME_STORAGE_KEY,
  parseThemeMode,
  resolveTheme,
  nextMode,
  type ResolvedTheme,
  type ThemeMode,
} from "../utils/theme";

const MEDIA = "(prefers-color-scheme: dark)";

function prefersDark(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia(MEDIA).matches
  );
}

/** Apply the resolved theme as a class on <html> so CSS-variable overrides
 * (the inverted gray ramp + CodeMirror palette in index.css) take effect. */
function applyTheme(resolved: ResolvedTheme) {
  const root = document.documentElement;
  root.classList.toggle("light", resolved === "light");
  root.classList.toggle("dark", resolved === "dark");
  root.style.colorScheme = resolved;
}

export interface UseTheme {
  mode: ThemeMode;
  resolved: ResolvedTheme;
  setMode: (mode: ThemeMode) => void;
  cycle: () => void;
}

export function useTheme(): UseTheme {
  const [mode, setModeState] = useState<ThemeMode>(() =>
    parseThemeMode(
      typeof localStorage !== "undefined"
        ? localStorage.getItem(THEME_STORAGE_KEY)
        : null,
    ),
  );
  const [resolved, setResolved] = useState<ResolvedTheme>(() =>
    resolveTheme(mode, prefersDark()),
  );

  // Re-resolve + paint whenever the mode changes.
  useEffect(() => {
    const r = resolveTheme(mode, prefersDark());
    setResolved(r);
    applyTheme(r);
  }, [mode]);

  // In system mode, follow OS changes live.
  useEffect(() => {
    if (mode !== "system") return;
    if (typeof window === "undefined" || !window.matchMedia) return;
    const mq = window.matchMedia(MEDIA);
    const onChange = () => {
      const r = resolveTheme("system", mq.matches);
      setResolved(r);
      applyTheme(r);
    };
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [mode]);

  const setMode = useCallback((next: ThemeMode) => {
    setModeState(next);
    try {
      localStorage.setItem(THEME_STORAGE_KEY, next);
    } catch {
      // Non-fatal — private mode / storage disabled just loses persistence.
    }
  }, []);

  const cycle = useCallback(() => setMode(nextMode(mode)), [mode, setMode]);

  return { mode, resolved, setMode, cycle };
}
