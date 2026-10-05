import { describe, expect, it } from "vitest";
import { cpToUtf16 } from "./offsets";

describe("cpToUtf16", () => {
  it("is the identity without characters outside the basic plane", () => {
    expect(cpToUtf16("สามปี", 3)).toBe(3);
    expect(cpToUtf16("abc", 10)).toBe(3);
    expect(cpToUtf16("abc", -1)).toBe(0);
  });

  it("counts a character outside the basic plane once", () => {
    const text = "a😀b😀c";
    expect(cpToUtf16(text, 0)).toBe(0);
    expect(cpToUtf16(text, 1)).toBe(1);
    expect(cpToUtf16(text, 2)).toBe(3); // after the first emoji
    expect(cpToUtf16(text, 4)).toBe(6);
    expect(cpToUtf16(text, 5)).toBe(7); // the end
    expect(cpToUtf16(text, 99)).toBe(7);
  });
});
