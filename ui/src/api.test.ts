import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiGet, tokenFromHash } from "./api";

describe("tokenFromHash", () => {
  it("reads the token from the fragment", () => {
    expect(tokenFromHash("#token=abc")).toBe("abc");
    expect(tokenFromHash("#x=1&token=a%2Bb")).toBe("a+b");
    expect(tokenFromHash("")).toBeNull();
  });
});

describe("apiGet", () => {
  beforeEach(() => {
    window.location.hash = "#token=t0k";
  });

  it("sends the token header and returns JSON", async () => {
    const fetchImpl = vi.fn(async () => new Response(JSON.stringify({ status: "ok" })));
    await expect(apiGet("/api/health", fetchImpl)).resolves.toEqual({ status: "ok" });
    expect(fetchImpl).toHaveBeenCalledWith("/api/health", {
      headers: { "X-Tamra-Token": "t0k" },
    });
  });

  it("throws on HTTP errors", async () => {
    const fetchImpl = vi.fn(async () => new Response("no", { status: 401 }));
    await expect(apiGet("/api/health", fetchImpl)).rejects.toThrow("/api/health: HTTP 401");
  });
});
