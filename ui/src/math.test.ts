import { describe, expect, it } from "vitest";
import { renderMath, splitMath } from "./math";

/** The formulas of a text as [raw text of the range, src, display]. */
function formulas(text: string) {
  return splitMath(text).flatMap((s) =>
    s.kind === "math" ? [[text.slice(s.start, s.end), s.src, s.display]] : [],
  );
}

const plain = (text: string) => [{ kind: "text", text, start: 0, end: text.length }];

describe("splitMath", () => {
  it("leaves prices as text, in English and in Thai", () => {
    for (const text of ["It costs $5 and $10 in total.", "ราคา $5 และ $10 ต่อเดือน", "Pay $5, get $10 back."]) {
      expect(splitMath(text)).toEqual(plain(text));
    }
  });

  it("leaves prices as text even when TeX-like characters sit between them", () => {
    for (const text of [
      "ราคา $5 ต่อ user_month และ $10",
      "Plans: $5 per user_month and $10 per {seat}",
      "$5 and $10",
      "ราคา $5 และ $10",
      "From $5^2 to $10_x",
    ]) {
      expect(splitMath(text)).toEqual(plain(text));
    }
  });

  it("closes only at a dollar that is not followed by a digit", () => {
    expect(formulas("Pay $x_1 + 5$ then $10")).toEqual([["$x_1 + 5$", "x_1 + 5", false]]);
    expect(formulas("Is $a$5 or $b^2$?")).toEqual([["$b^2$", "b^2", false]]);
  });

  it("reads a formula that starts with a digit", () => {
    expect(formulas("Area is $2\\pi r$ and $10^3$")).toEqual([
      ["$2\\pi r$", "2\\pi r", false],
      ["$10^3$", "10^3", false],
    ]);
  });

  it("reads $ ... $ with spaces inside", () => {
    const text = "Speed is $ \\frac{\\pi}{4} $ rad/s";
    expect(formulas(text)).toEqual([["$ \\frac{\\pi}{4} $", " \\frac{\\pi}{4} ", false]]);
    expect(splitMath(text).map((s) => s.kind)).toEqual(["text", "math", "text"]);
  });

  it("reads \\( ... \\) as inline and keeps the raw range", () => {
    expect(formulas("Area \\(x^2\\) here")).toEqual([["\\(x^2\\)", "x^2", false]]);
  });

  it("reads $$ ... $$ and \\[ ... \\] as display, across lines", () => {
    expect(formulas("A $$a^2\n+ b^2$$ B")).toEqual([["$$a^2\n+ b^2$$", "a^2\n+ b^2", true]]);
    expect(formulas("A \\[ y_1 \\] B")).toEqual([["\\[ y_1 \\]", " y_1 ", true]]);
  });

  it("needs a TeX mark in a single-dollar span, and keeps it on one line", () => {
    expect(formulas("$x$ and $x_1$ and $a^b$ and ${c}$")).toEqual([
      ["$x_1$", "x_1", false],
      ["$a^b$", "a^b", false],
      ["${c}$", "{c}", false],
    ]);
    expect(formulas("start $a^2\nend$")).toEqual([]);
  });

  it("does not close at an escaped dollar", () => {
    expect(formulas("$a \\$ b^2$ done")).toEqual([["$a \\$ b^2$", "a \\$ b^2", false]]);
    expect(formulas("cost \\$5 and \\$6")).toEqual([]);
  });

  it("keeps an unclosed delimiter as text, as while an answer streams", () => {
    for (const text of ["half $\\frac{", "half $$a^2", "half \\(x^2", "half \\[ x", "$$ $$"]) {
      expect(formulas(text)).toEqual([]);
      expect(splitMath(text)).toEqual(plain(text));
    }
  });

  it("covers the text without gaps", () => {
    const text = "a $x^2$ b \\(y_1\\) c $$z^3$$ d [1]";
    const segments = splitMath(text);
    expect(segments.map((s) => text.slice(s.start, s.end)).join("")).toBe(text);
    expect(segments.at(-1)).toEqual({ kind: "text", text: " d [1]", start: 27, end: 33 });
  });

  it("leaves a citation marker right after a formula to the text after it", () => {
    expect(splitMath("Rate is $x^2$[1] ok").map((s) => (s.kind === "text" ? s.text : "<math>"))).toEqual([
      "Rate is ",
      "<math>",
      "[1] ok",
    ]);
  });
});

describe("renderMath", () => {
  it("renders MathML", () => {
    const html = renderMath("\\frac{\\pi}{4}", false);
    expect(html).toContain("<math");
    expect(html).toContain("<mfrac");
  });

  it("escapes markup typed as a formula", () => {
    const html = renderMath("<script>alert(1)</script><img src=x onerror=alert(2)> x^2", false);
    expect(html).not.toContain("<script");
    expect(html).not.toContain("<img");
  });

  it("does not throw on invalid TeX", () => {
    expect(() => renderMath("\\frac{", false)).not.toThrow();
  });
});
