import { describe, expect, it } from "vitest";
import { cpToUtf16, utf16ToCp } from "./offsets";

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

describe("utf16ToCp", () => {
  it("is the identity without characters outside the basic plane", () => {
    expect(utf16ToCp("สามปี", 3)).toBe(3);
    expect(utf16ToCp("abc", 10)).toBe(3);
    expect(utf16ToCp("abc", -1)).toBe(0);
  });

  it("counts a character outside the basic plane once", () => {
    const text = "a😀b😀c";
    expect(utf16ToCp(text, 0)).toBe(0);
    expect(utf16ToCp(text, 1)).toBe(1);
    expect(utf16ToCp(text, 3)).toBe(2); // after the first emoji
    expect(utf16ToCp(text, 6)).toBe(4);
    expect(utf16ToCp(text, 7)).toBe(5); // the end
    expect(utf16ToCp(text, 99)).toBe(5);
  });

  it("is the inverse of cpToUtf16", () => {
    const text = "a😀b😀c";
    for (let cp = 0; cp <= 5; cp++) expect(utf16ToCp(text, cpToUtf16(text, cp))).toBe(cp);
  });
});
