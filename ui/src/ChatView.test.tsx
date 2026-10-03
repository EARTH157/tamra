import { afterEach, describe, expect, it, vi } from "vitest";
import ChatView from "./ChatView";
import { click, json, type Mounted, mockFetch, mount, sse, typeInto } from "./test-utils";

let view: Mounted | undefined;

afterEach(async () => {
  await view?.unmount();
  view = undefined;
  vi.unstubAllGlobals();
});

const chat = { id: 5, title: "", created_at: "t", updated_at: "t" };
const source = {
  n: 1,
  file: "lease.pdf",
  label: "p. 2",
  text: "The lease term is three years.",
  location: { kind: "pdf" },
};

describe("ChatView", () => {
  it("streams an answer and opens its source", async () => {
    let saved = false;
    mockFetch({
      "GET /api/chats/5": () =>
        json({
          ...chat,
          messages: saved
            ? [
                {
                  id: 1,
                  role: "user",
                  content: "How long?",
                  provider: null,
                  model: null,
                  created_at: "t",
                  sources: [],
                },
                {
                  id: 2,
                  role: "assistant",
                  content: "Three years [1].",
                  provider: "local",
                  model: "qwen",
                  created_at: "t",
                  sources: [source],
                },
              ]
            : [],
        }),
      "POST /api/chats/5/messages": () => {
        saved = true;
        return sse([
          { type: "sources", sources: [source] },
          { type: "token", text: "Three years " },
          { type: "token", text: "[1]." },
          { type: "done", message_id: 2 },
        ]);
      },
    });
    const onAnswered = vi.fn();
    view = await mount(
      <ChatView chatId={5} createChat={vi.fn()} onBusyChange={vi.fn()} onAnswered={onAnswered} />,
    );
    await typeInto(view.container.querySelector("textarea"), "How long?");
    await click(view.container.querySelector('button[type="submit"]'));
    expect(onAnswered).toHaveBeenCalled();
    expect(view.container.querySelector(".message.assistant")?.textContent).toContain(
      "Three years 1.",
    );
    await click(view.container.querySelector("button.cite"));
    expect(view.container.querySelector(".source-panel")?.textContent).toContain(
      "The lease term is three years.",
    );
  });

  it("creates the chat on the first question", async () => {
    const calls = mockFetch({
      "POST /api/chats/9/messages": () => sse([{ type: "done", message_id: 1 }]),
      "GET /api/chats/9": () => json({ ...chat, id: 9, messages: [] }),
    });
    const createChat = vi.fn(async () => 9);
    view = await mount(
      <ChatView chatId={null} createChat={createChat} onBusyChange={vi.fn()} onAnswered={vi.fn()} />,
    );
    await typeInto(view.container.querySelector("textarea"), "first question");
    await click(view.container.querySelector('button[type="submit"]'));
    expect(createChat).toHaveBeenCalledTimes(1);
    expect(calls.map((c) => c.key)).toEqual(["POST /api/chats/9/messages", "GET /api/chats/9"]);
  });

  it("shows an error from the core", async () => {
    mockFetch({
      "GET /api/chats/5": () => json({ ...chat, messages: [] }),
      "POST /api/chats/5/messages": () =>
        sse([{ type: "error", message: "Choose a folder of documents first." }]),
    });
    view = await mount(
      <ChatView chatId={5} createChat={vi.fn()} onBusyChange={vi.fn()} onAnswered={vi.fn()} />,
    );
    await typeInto(view.container.querySelector("textarea"), "q");
    await click(view.container.querySelector('button[type="submit"]'));
    expect(view.container.querySelector('[role="alert"]')?.textContent).toBe(
      "Choose a folder of documents first.",
    );
  });
});
