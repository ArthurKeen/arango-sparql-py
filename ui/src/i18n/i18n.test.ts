import { describe, it, expect } from "vitest";
import { t } from "./index";
import { messages, type MessageKey } from "./en";

describe("t()", () => {
  it("returns the catalogue string for a known key", () => {
    expect(t("app.title")).toBe("Arango SPARQL");
    expect(t("action.translate")).toBe("Translate");
  });

  it("interpolates {name} placeholders", () => {
    expect(t("status.rows", { count: 42 })).toBe("42 rows");
    expect(t("status.error", { message: "boom" })).toBe("Error: boom");
  });

  it("leaves unmatched placeholders intact and ignores extra vars", () => {
    expect(t("status.rows", { nope: 1 })).toBe("{count} rows");
  });

  it("falls back to the key for an unknown key", () => {
    // Cast to exercise the runtime fallback path the type would prevent.
    expect(t("missing.key" as MessageKey)).toBe("missing.key");
  });

  it("every catalogue value is a non-empty string", () => {
    for (const [key, value] of Object.entries(messages)) {
      expect(typeof value, key).toBe("string");
      expect(value.length, key).toBeGreaterThan(0);
    }
  });
});
