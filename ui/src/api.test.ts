import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, api, initToken, streamAnswer, tokenFromHash } from "./api";
import type { AnswerEvent } from "./types";

describe("tokenFromHash", () => {
  it("reads the token from the fragment", () => {
    expect(tokenFromHash("#token=abc")).toBe("abc");
    expect(tokenFromHash("#x=1&token=a%2Bb")).toBe("a+b");
    expect(tokenFromHash("")).toBeNull();
  });

  it("returns null for a malformed escape", () => {
    expect(tokenFromHash("#token=%E0%A4%A")).toBeNull();
  });
});

describe("initToken", () => {
  beforeEach(() => sessionStorage.clear());

  it("reads the token once and removes it from the address", async () => {
    window.location.hash = "#token=t0k";
    initToken();
    expect(window.location.hash).toBe("");
    const fetchImpl = vi.fn(async () => new Response("{}"));
    await api("GET", "/api/health", undefined, fetchImpl);
    expect(fetchImpl).toHaveBeenCalledWith(
      "/api/health",
      expect.objectContaining({ headers: { "X-Tamra-Token": "t0k" } }),
    );
  });

  it("keeps the token when the window is reloaded", async () => {
    window.location.hash = "#token=again";
    initToken();
    initToken(); // a reload: the fragment is gone, the session still has the token
    const fetchImpl = vi.fn(async () => new Response("{}"));
    await api("GET", "/api/health", undefined, fetchImpl);
    expect(fetchImpl).toHaveBeenCalledWith(
      "/api/health",
      expect.objectContaining({ headers: { "X-Tamra-Token": "again" } }),
    );
  });
});

describe("api", () => {
  beforeEach(() => {
    sessionStorage.clear();
    window.location.hash = "#token=t0k";
    initToken();
  });

  it("sends JSON bodies and returns JSON", async () => {
    const fetchImpl = vi.fn(async () => new Response(JSON.stringify({ id: 1 })));
    await expect(api("PUT", "/api/collection", { folder_path: "C:/d" }, fetchImpl)).resolves.toEqual(
      { id: 1 },
    );
    expect(fetchImpl).toHaveBeenCalledWith("/api/collection", {
      method: "PUT",
      headers: { "X-Tamra-Token": "t0k", "Content-Type": "application/json" },
      body: '{"folder_path":"C:/d"}',
    });
  });

  it("returns nothing for 204", async () => {
    const fetchImpl = vi.fn(async () => new Response(null, { status: 204 }));
    await expect(api("DELETE", "/api/chats/1", undefined, fetchImpl)).resolves.toBeUndefined();
  });

  it("raises the server's message", async () => {
    const fetchImpl = vi.fn(
      async () => new Response(JSON.stringify({ detail: "Folder not found: X" }), { status: 400 }),
    );
    const error = await api("PUT", "/api/collection", {}, fetchImpl).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(400);
    expect((error as ApiError).message).toBe("Folder not found: X");
  });

  it("falls back to the status when the body is not JSON", async () => {
    const fetchImpl = vi.fn(async () => new Response("no", { status: 401 }));
    await expect(api("GET", "/api/health", undefined, fetchImpl)).rejects.toThrow("HTTP 401");
  });
});

describe("streamAnswer", () => {
  it("parses events when a Thai character is split across chunks", async () => {
    const bytes = new TextEncoder().encode(
      'data: {"type":"token","text":"สาม"}\n\ndata: {"type":"done","message_id":7}\n\n',
    );
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(bytes.slice(0, 31)); // ends inside the first Thai character
        controller.enqueue(bytes.slice(31));
        controller.close();
      },
    });
    const fetchImpl = vi.fn(async () => new Response(body));
    const events: AnswerEvent[] = [];
    await streamAnswer(3, "q", (event) => events.push(event), { mode: "search", think: true }, fetchImpl);
    expect(events).toEqual([
      { type: "token", text: "สาม" },
      { type: "done", message_id: 7 },
    ]);
    expect(fetchImpl).toHaveBeenCalledWith(
      "/api/chats/3/messages",
      expect.objectContaining({ method: "POST", body: '{"content":"q","mode":"search","think":true}' }),
    );
  });

  it("asks for an answer without thinking by default", async () => {
    const done = 'data: {"type":"done","message_id":1}\n\n';
    const fetchImpl = vi.fn(async () => new Response(done));
    await streamAnswer(3, "q", () => {}, undefined, fetchImpl);
    expect(fetchImpl).toHaveBeenCalledWith(
      "/api/chats/3/messages",
      expect.objectContaining({ body: '{"content":"q","mode":"answer","think":false}' }),
    );
  });

  it("raises an HTTP error before streaming", async () => {
    const fetchImpl = vi.fn(async () => new Response("{}", { status: 422 }));
    await expect(streamAnswer(3, "", () => {}, undefined, fetchImpl)).rejects.toThrow("HTTP 422");
  });
});
