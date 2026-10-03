import { describe, expect, it } from "vitest";
import { splitCitations } from "./citations";

describe("splitCitations", () => {
  it("turns [n] markers into citations", () => {
    expect(splitCitations("Three years [1].", 2)).toEqual([
      { kind: "text", text: "Three years " },
      { kind: "cite", n: 1 },
      { kind: "text", text: "." },
    ]);
  });

  it("handles adjacent and comma-separated markers", () => {
    expect(splitCitations("A [1][2] B [1, 2]", 2)).toEqual([
      { kind: "text", text: "A " },
      { kind: "cite", n: 1 },
      { kind: "cite", n: 2 },
      { kind: "text", text: " B " },
      { kind: "cite", n: 1 },
      { kind: "cite", n: 2 },
    ]);
  });

  it("leaves numbers without a source as text", () => {
    expect(splitCitations("See [7] and [0].", 4)).toEqual([
      { kind: "text", text: "See [7] and [0]." },
    ]);
  });

  it("works inside Thai and Chinese text", () => {
    expect(splitCitations("สามปี[1]三年[2]", 2)).toEqual([
      { kind: "text", text: "สามปี" },
      { kind: "cite", n: 1 },
      { kind: "text", text: "三年" },
      { kind: "cite", n: 2 },
    ]);
  });
});
