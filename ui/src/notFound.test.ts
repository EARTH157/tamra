import { describe, expect, it } from "vitest";
import { isNotFound, NOT_FOUND_REPLIES } from "./notFound";

describe("isNotFound", () => {
  it("matches the core's not-found replies only when there are no sources", () => {
    for (const reply of NOT_FOUND_REPLIES) expect(isNotFound(reply, 0)).toBe(true);
    expect(isNotFound("Not found in the documents.", 1)).toBe(false);
    expect(isNotFound("The lease is three years.", 0)).toBe(false);
    expect(isNotFound("", 0)).toBe(false);
  });

  it("ignores surrounding whitespace", () => {
    expect(isNotFound("  ไม่พบข้อมูลนี้ในเอกสาร\n", 0)).toBe(true);
  });
});
