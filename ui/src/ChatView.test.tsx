import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import ChatView from "./ChatView";
import { fileName, folderOf } from "./SourcePanel";
import { click, json, type Mounted, mockFetch, mount, settle, sse, typeInto } from "./test-utils";

let view: Mounted | undefined;

afterEach(async () => {
  await view?.unmount();
  view = undefined;
  vi.unstubAllGlobals();
});

const base = {
  createChat: vi.fn(),
  onBusyChange: vi.fn(),
  onAnswered: vi.fn(),
  onOpenSettings: vi.fn(),
};
const chat = { id: 5, title: "", created_at: "t", updated_at: "t" };
const source = {
  n: 1,
  file: "lease.pdf",
  label: "p. 2",
  text: "The lease term is three years.",
  location: { kind: "pdf" },
};


/** Like App: creating a chat makes it the active one. */
function Host({ id }: { id: number }) {
  const [chatId, setChatId] = useState<number | null>(null);
  return (
    <ChatView
      {...base}
      chatId={chatId}
      createChat={async () => {
        setChatId(id);
        return id;
      }}
    />
  );
}

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
      "POST /api/attribution": () =>
        json({
          matches: [
            {
              n: 1,
              start: 0,
              end: 30,
              text: "The lease term is three years.",
              label: "strong",
              file_id: 3,
              file: "lease.pdf",
              location_label: "p. 2",
              changed: false,
            },
          ],
        }),
      "GET /api/sources/2/1/locate?start=0&end=30": () => json({ detail: "File not found." }, 404),
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
      <ChatView {...base} chatId={5} onAnswered={onAnswered} />,
    );
    await typeInto(view.container.querySelector("textarea"), "How long?");
    await click(view.container.querySelector('button[type="submit"]'));
    expect(onAnswered).toHaveBeenCalled();
    expect(view.container.querySelector(".message.assistant")?.textContent).toContain(
      "Three years 1.",
    );
    const answer = view.container.querySelector(".message.assistant");
    expect(answer?.textContent).toContain("qwen · local");
    expect(answer?.textContent).toContain("Click a number to see the passage it came from.");
    expect(answer?.querySelector(".source-card")?.textContent).toContain("lease.pdf");
    expect(answer?.querySelector(".source-card")?.textContent).toContain("p. 2");
    await click(view.container.querySelector("button.cite"));
    expect(view.container.querySelector(".source-panel")?.textContent).toContain(
      "The lease term is three years.",
    );
    expect(view.container.querySelector("button.cite")?.classList.contains("active")).toBe(true);
    await click(view.container.querySelector('button[aria-label="Close source"]'));
    expect(view.container.querySelector(".source-panel")).toBeNull();
    expect(view.container.querySelector("button.cite")?.classList.contains("active")).toBe(false);
  });

  it("creates the chat on the first question", async () => {
    const calls = mockFetch({
      "POST /api/chats/9/messages": () => sse([{ type: "done", message_id: 1 }]),
      "GET /api/chats/9": () => json({ ...chat, id: 9, messages: [] }),
    });
    const createChat = vi.fn(async () => 9);
    view = await mount(
      <ChatView {...base} chatId={null} createChat={createChat} />,
    );
    await typeInto(view.container.querySelector("textarea"), "first question");
    await click(view.container.querySelector('button[type="submit"]'));
    expect(createChat).toHaveBeenCalledTimes(1);
    expect(calls.map((c) => c.key).filter((key) => key !== "GET /api/models")).toEqual([
      "POST /api/chats/9/messages",
      "GET /api/chats/9",
    ]);
  });

  it("stays usable when the answer of a new chat fails", async () => {
    mockFetch({
      "POST /api/chats/9/messages": () => json({ detail: "The model is not ready." }, 500),
      "GET /api/chats/9": () => json({ ...chat, id: 9, messages: [] }),
    });
    view = await mount(<Host id={9} />);
    await typeInto(view.container.querySelector("textarea"), "first question");
    await click(view.container.querySelector('button[type="submit"]'));
    expect(view.container.querySelector('[role="alert"]')?.textContent).toBe(
      "The model is not ready.",
    );
    expect(view.container.querySelector("textarea")?.disabled).toBe(false);
  });

  it("stays usable when the saved chat cannot be reloaded", async () => {
    mockFetch({
      "POST /api/chats/9/messages": () => json({ detail: "The model is not ready." }, 500),
      "GET /api/chats/9": () => json({ detail: "gone" }, 500),
    });
    view = await mount(<Host id={9} />);
    await typeInto(view.container.querySelector("textarea"), "first question");
    await click(view.container.querySelector('button[type="submit"]'));
    expect(view.container.querySelector("textarea")?.disabled).toBe(false);
  });

  it("sends a cancel request when Stop is pressed during a stream", async () => {
    let finish: () => void = () => {};
    const open = new ReadableStream<Uint8Array>({
      start(controller) {
        const encoder = new TextEncoder();
        controller.enqueue(encoder.encode(`data: ${JSON.stringify({ type: "token", text: "Hi" })}\n\n`));
        finish = () => controller.close();
      },
    });
    const calls = mockFetch({
      "GET /api/chats/5": () => json({ ...chat, messages: [] }),
      "POST /api/chats/5/messages": () =>
        new Response(open, { headers: { "Content-Type": "text/event-stream" } }),
      "POST /api/answer/cancel": () => json({ cancelled: true }),
    });
    view = await mount(
      <ChatView {...base} chatId={5} />,
    );
    await typeInto(view.container.querySelector("textarea"), "q");
    await click(view.container.querySelector('button[type="submit"]'));
    expect(view.container.querySelector('button[type="submit"]')).toBeNull();
    await click(view.container.querySelector('button[aria-label="Stop"]'));
    expect(calls.map((c) => c.key)).toContain("POST /api/answer/cancel");
    finish();
    await settle();
  });

  it("shows an error from the core", async () => {
    mockFetch({
      "GET /api/chats/5": () => json({ ...chat, messages: [] }),
      "POST /api/chats/5/messages": () =>
        sse([{ type: "error", message: "Choose a folder of documents first." }]),
    });
    view = await mount(
      <ChatView {...base} chatId={5} />,
    );
    await typeInto(view.container.querySelector("textarea"), "q");
    await click(view.container.querySelector('button[type="submit"]'));
    expect(view.container.querySelector('[role="alert"]')?.textContent).toBe(
      "Choose a folder of documents first.",
    );
  });

  it("shows the empty state of a new chat", async () => {
    mockFetch({});
    view = await mount(
      <ChatView {...base} chatId={null} />,
    );
    expect(view.container.textContent).toContain("Ask your documents");
    expect(view.container.textContent).toContain(
      "Every answer cites the passage it came from, so you can check it.",
    );
    expect(view.container.textContent).toContain("Enter to send · Shift+Enter for a new line");
  });

  it("shows a saved not-found reply as a card", async () => {
    mockFetch({
      "GET /api/chats/5": () =>
        json({
          ...chat,
          messages: [
            {
              id: 2,
              role: "assistant",
              content: "ไม่พบข้อมูลนี้ในเอกสาร",
              provider: null,
              model: null,
              created_at: "t",
              sources: [],
            },
          ],
        }),
    });
    view = await mount(
      <ChatView {...base} chatId={5} collectionName="HR Documents" />,
    );
    const card = view.container.querySelector(".not-found");
    expect(card?.textContent).toContain("Not found in HR Documents");
    expect(card?.textContent).toContain(
      "Tamra answers only from your documents, so it will not guess.",
    );
    expect(view.container.textContent).not.toContain("ไม่พบข้อมูลนี้ในเอกสาร");
  });

  it("shows searching, then the not-found card once the stream is done", async () => {
    const encoder = new TextEncoder();
    let push: (event: unknown) => void = () => {};
    let finish: () => void = () => {};
    const open = new ReadableStream<Uint8Array>({
      start(controller) {
        push = (event) =>
          controller.enqueue(encoder.encode(`data: ${JSON.stringify(event)}\n\n`));
        finish = () => controller.close();
      },
    });
    mockFetch({
      "GET /api/chats/5": () => json({ ...chat, messages: [] }),
      "POST /api/chats/5/messages": () =>
        new Response(open, { headers: { "Content-Type": "text/event-stream" } }),
    });
    view = await mount(
      <ChatView {...base} chatId={5} collectionName="Contracts" />,
    );
    await typeInto(view.container.querySelector("textarea"), "notice period?");
    await click(view.container.querySelector('button[type="submit"]'));
    expect(view.container.textContent).toContain("Searching your documents…");
    push({ type: "sources", sources: [] });
    push({ type: "token", text: "Not found in the documents." });
    await settle();
    expect(view.container.querySelector(".not-found")).toBeNull();
    push({ type: "done", message_id: 3 });
    await settle();
    expect(view.container.textContent).not.toContain("Searching your documents…");
    expect(view.container.querySelector(".not-found")?.textContent).toContain(
      "Not found in Contracts",
    );
    finish();
    await settle();
  });

  it("keeps a source opened while streaming open once the answer is saved", async () => {
    const encoder = new TextEncoder();
    let push: (event: unknown) => void = () => {};
    let finish: () => void = () => {};
    let saved = false;
    const open = new ReadableStream<Uint8Array>({
      start(controller) {
        push = (event) =>
          controller.enqueue(encoder.encode(`data: ${JSON.stringify(event)}\n\n`));
        finish = () => controller.close();
      },
    });
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
      "POST /api/chats/5/messages": () =>
        new Response(open, { headers: { "Content-Type": "text/event-stream" } }),
    });
    view = await mount(
      <ChatView {...base} chatId={5} />,
    );
    await typeInto(view.container.querySelector("textarea"), "How long?");
    await click(view.container.querySelector('button[type="submit"]'));
    push({ type: "sources", sources: [source] });
    push({ type: "token", text: "Three years [1]." });
    await settle();
    await click(view.container.querySelector("button.cite"));
    expect(view.container.querySelector(".source-panel")?.textContent).toContain(
      "The lease term is three years.",
    );
    saved = true;
    push({ type: "done", message_id: 2 });
    finish();
    await settle();
    expect(view.container.querySelector('button[aria-label="Send"]')).not.toBeNull(); // done
    expect(view.container.querySelector(".source-panel")?.textContent).toContain(
      "The lease term is three years.",
    );
    expect(view.container.querySelector("button.cite")?.classList.contains("active")).toBe(true);
  });
});

describe("source names", () => {
  it("puts the file name first and keeps its folder", () => {
    expect(fileName("2.1 AVRS/2.1.4-more_detail/user-manual.pdf")).toBe("user-manual.pdf");
    expect(folderOf("2.1 AVRS/2.1.4-more_detail/user-manual.pdf")).toBe("2.1 AVRS/2.1.4-more_detail");
    expect(fileName("lease.pdf")).toBe("lease.pdf");
    expect(folderOf("lease.pdf")).toBe("");
  });
});
