import { describe, it, expect } from "vitest";
import {
  parseThemeMode,
  resolveTheme,
  nextMode,
  themeModeLabel,
} from "./theme";

describe("parseThemeMode", () => {
  it("passes through valid modes", () => {
    expect(parseThemeMode("dark")).toBe("dark");
    expect(parseThemeMode("light")).toBe("light");
    expect(parseThemeMode("system")).toBe("system");
  });
  it("defaults junk to system", () => {
    expect(parseThemeMode(null)).toBe("system");
    expect(parseThemeMode("neon")).toBe("system");
  });
});

describe("resolveTheme", () => {
  it("honours explicit modes regardless of OS", () => {
    expect(resolveTheme("dark", false)).toBe("dark");
    expect(resolveTheme("light", true)).toBe("light");
  });
  it("follows the OS in system mode", () => {
    expect(resolveTheme("system", true)).toBe("dark");
    expect(resolveTheme("system", false)).toBe("light");
  });
});

describe("nextMode", () => {
  it("cycles system -> dark -> light -> system", () => {
    expect(nextMode("system")).toBe("dark");
    expect(nextMode("dark")).toBe("light");
    expect(nextMode("light")).toBe("system");
  });
});

describe("themeModeLabel", () => {
  it("labels each mode", () => {
    expect(themeModeLabel("dark")).toBe("Dark");
    expect(themeModeLabel("light")).toBe("Light");
    expect(themeModeLabel("system")).toBe("System");
  });
});
