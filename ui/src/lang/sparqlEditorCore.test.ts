import { describe, it, expect } from "vitest";
import { core, CORE_API_SHAPE } from "./sparqlEditorCore";

// The core is a plain JS file in the Python package; these pin the typed
// view of it to the real object so a rename on either side fails here.
describe("sparqlEditorCore", () => {
  it("exposes exactly the typed API", () => {
    expect(Object.keys(core).sort()).toEqual(Object.keys(CORE_API_SHAPE).sort());
  });

  it("is frozen and names editor vocabulary v1", () => {
    expect(Object.isFrozen(core)).toBe(true);
    expect(core.VOCAB_CONTRACT).toBe("editor-vocabulary/v1");
  });

  it("registers the same object on globalThis (the classic-script loader)", () => {
    expect((globalThis as { SparqlEditorCore?: unknown }).SparqlEditorCore).toBe(core);
  });
});
