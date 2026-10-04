import { describe, expect, it } from "vitest";
import { createSseParser } from "./sse";

describe("createSseParser", () => {
  it("emits each event once it is complete, across chunk boundaries", () => {
    const seen: string[] = [];
    const feed = createSseParser((data) => seen.push(data));
    feed('data: {"a":1}\n\ndata: {"b"');
    expect(seen).toEqual(['{"a":1}']);
    feed(":2}\n");
    expect(seen).toEqual(['{"a":1}']);
    feed("\n");
    expect(seen).toEqual(['{"a":1}', '{"b":2}']);
  });

  it("joins multi-line data and ignores comments", () => {
    const seen: string[] = [];
    const feed = createSseParser((data) => seen.push(data));
    feed(": keep-alive\n\ndata: one\ndata: two\n\n");
    expect(seen).toEqual(["one\ntwo"]);
  });
});
