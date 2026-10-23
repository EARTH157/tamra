import { describe, expect, it } from "vitest";
import { compactTokens } from "./ContextMeter";

describe("compactTokens", () => {
  it("shortens thousands", () => {
    expect([950, 1000, 2150, 8192, 32000].map(compactTokens)).toEqual([
      "950",
      "1k",
      "2.2k",
      "8.2k",
      "32k",
    ]);
  });
});
