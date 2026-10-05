import { describe, expect, it } from "vitest";
import { splitCitations } from "./citations";

describe("splitCitations", () => {
  it("turns [n] markers into citations", () => {
    expect(splitCitations("Three years [1].", 2)).toEqual([
      { kind: "text", text: "Three years ", start: 0, end: 12 },
      { kind: "cite", n: 1, start: 12, end: 15 },
      { kind: "text", text: ".", start: 15, end: 16 },
    ]);
  });

  it("handles adjacent and comma-separated markers", () => {
    expect(splitCitations("A [1][2] B [1, 2]", 2)).toEqual([
      { kind: "text", text: "A ", start: 0, end: 2 },
      { kind: "cite", n: 1, start: 2, end: 5 },
      { kind: "cite", n: 2, start: 5, end: 8 },
      { kind: "text", text: " B ", start: 8, end: 11 },
      { kind: "cite", n: 1, start: 11, end: 17 },
      { kind: "cite", n: 2, start: 11, end: 17 },
    ]);
  });

  it("leaves numbers without a source as text", () => {
    expect(splitCitations("See [7] and [0].", 4)).toEqual([
      { kind: "text", text: "See [7] and [0].", start: 0, end: 16 },
    ]);
  });

  it("works inside Thai and Chinese text", () => {
    expect(splitCitations("สามปี[1]三年[2]", 2)).toEqual([
      { kind: "text", text: "สามปี", start: 0, end: 5 },
      { kind: "cite", n: 1, start: 5, end: 8 },
      { kind: "text", text: "三年", start: 8, end: 10 },
      { kind: "cite", n: 2, start: 10, end: 13 },
    ]);
  });
});
