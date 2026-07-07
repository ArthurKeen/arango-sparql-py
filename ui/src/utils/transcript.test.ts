import { describe, it, expect } from "vitest";
import { appendTurn, turnStatusLabel, type TranscriptTurn } from "./transcript";

const turn = (id: string, ok = true): TranscriptTurn => ({
  id,
  question: `q${id}`,
  sparql: ok ? "SELECT * WHERE {}" : null,
  ok,
  timestamp: Number(id),
});

describe("appendTurn", () => {
  it("appends to the end (chat order)", () => {
    const r = appendTurn([turn("1")], turn("2"));
    expect(r.map((t) => t.id)).toEqual(["1", "2"]);
  });

  it("caps to the most recent N", () => {
    let list: TranscriptTurn[] = [];
    for (let i = 1; i <= 5; i++) list = appendTurn(list, turn(String(i)), 3);
    expect(list.map((t) => t.id)).toEqual(["3", "4", "5"]);
  });

  it("does not mutate the input", () => {
    const orig = [turn("1")];
    appendTurn(orig, turn("2"));
    expect(orig.map((t) => t.id)).toEqual(["1"]);
  });
});

describe("turnStatusLabel", () => {
  it("labels ok vs failed", () => {
    expect(turnStatusLabel(turn("1", true))).toBe("SPARQL generated");
    expect(turnStatusLabel(turn("1", false))).toBe("Generation failed");
  });
});
