import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import ChatView from "./ChatView";
import { fileName, folderOf } from "./SourcePanel";
import {
  buttonByText,
  click,
  json,
  type Mounted,
  mockFetch,
  mount,
  settle,
  sse,
  typeInto,
} from "./test-utils";

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

  it("starts a general chat when the first question is asked in Chat mode", async () => {
    const createChat = vi.fn(async () => 9);
    mockFetch({
      "POST /api/chats/9/messages": () =>
        sse([
          { type: "token", text: "Hello!" },
          { type: "done", message_id: 2 },
        ]),
      "GET /api/chats/9": () => json({ ...chat, id: 9, project_id: null, scope: [], messages: [] }),
    });
    view = await mount(<ChatView {...base} chatId={null} createChat={createChat} />);
    await click(view.container.querySelector('button[aria-label^="How Tamra responds"]'));
    await click(
      [...document.querySelectorAll<HTMLElement>('[role="menuitemradio"]')].find((el) =>
        el.textContent?.startsWith("Chat"),
      ),
    );
    await typeInto(view.container.querySelector("textarea"), "hello");
    await click(view.container.querySelector('button[type="submit"]'));
    expect(createChat).toHaveBeenCalledWith(true);
  });

  it("shows the project notes as their own source, with no file to open", async () => {
    const writeText = vi.fn(async () => {});
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
    const notes = {
      n: 2,
      file: "Your project notes",
      file_id: null,
      label: "",
      text: "I am the tenant of unit 1204. I moved in on 3 March.",
      location: { kind: "notes" },
      project: null,
    };
    const calls = mockFetch({
      "GET /api/chats/5": () =>
        json({
          ...chat,
          messages: [
            {
              id: 2,
              role: "assistant",
              content: "The term is three years [1]. You moved in on 3 March [2].",
              provider: "local",
              model: "qwen",
              created_at: "t",
              sources: [source, notes],
            },
          ],
          context: null,
        }),
    });
    view = await mount(<ChatView {...base} chatId={5} />);
    const cards = [...view.container.querySelectorAll(".source-card")];
    expect(cards[1].textContent).toBe("2Your project notes"); // no location line
    await click(cards[1]);
    const panel = view.container.querySelector(".source-panel")!;
    expect(panel.querySelector(".source-title strong")?.textContent).toBe("Your project notes");
    expect(buttonByText(panel, "Open file")).toBeUndefined();
    expect(panel.textContent).toContain("I moved in on 3 March.");
    expect(calls.some((call) => call.key.includes("/locate"))).toBe(false); // no file to locate
    await click(buttonByText(view.container, "Copy"));
    expect(writeText).toHaveBeenCalledWith(
      "The term is three years [1]. You moved in on 3 March [2].\n\nSources:\n" +
        "[1] lease.pdf, p. 2\n[2] Your project notes",
    );
  });

  it("copies an answer with the sources it cites, and says it did", async () => {
    const writeText = vi.fn(async () => {});
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
    const sources = [
      source,
      { ...source, n: 2, file: "2026/rent.docx", label: "Rent" },
      { ...source, n: 3, file: "unused.md", label: "lines 1-3" },
    ];
    mockFetch({
      "GET /api/chats/5": () =>
        json({
          ...chat,
          messages: [
            {
              id: 2,
              role: "assistant",
              content: "Three years [1]. Rent is 18,500 baht [1, 2].",
              provider: "local",
              model: "qwen",
              created_at: "t",
              sources,
            },
          ],
        }),
    });
    view = await mount(<ChatView {...base} chatId={5} />);
    await click(buttonByText(view.container, "Copy"));
    expect(writeText).toHaveBeenCalledWith(
      "Three years [1]. Rent is 18,500 baht [1, 2].\n\nSources:\n" +
        "[1] lease.pdf, p. 2\n[2] 2026/rent.docx, Rent",
    );
    expect(view.container.querySelector(".copy-answer")?.textContent).toBe("Copied");
  });

  it("shows the context meter saved with a chat when it is opened again", async () => {
    mockFetch({
      "GET /api/chats/5": () =>
        json({
          ...chat,
          messages: [],
          context: {
            prompt_tokens: 900,
            context_tokens: 8192,
            reply_tokens: 1024,
            parts: { instructions: 100, sources: 0, history: 700, question: 100 },
            source_count: 0,
            history_kept: 3,
            history_total: 5,
            measured: { prompt_tokens: 620, generated_tokens: 42 },
          },
        }),
    });
    view = await mount(<ChatView {...base} chatId={5} />);
    const summary = view.container.querySelector(".context-summary");
    expect(summary?.textContent).toContain("Context 662 / 8.2k tokens");
    expect(summary?.textContent).toContain("2 older left out");
  });

  it("lists the project notes in the context details, and not when a context has none", async () => {
    const saved = (parts: Record<string, number>) => ({
      prompt_tokens: 0,
      context_tokens: 8192,
      reply_tokens: 1024,
      parts,
      source_count: 0,
      history_kept: 0,
      history_total: 0,
      measured: null,
    });
    const rows = () => [...view!.container.querySelectorAll(".context-parts li")].map((li) => li.textContent);
    mockFetch({
      "GET /api/chats/5": () =>
        json({
          ...chat,
          messages: [],
          context: saved({ instructions: 100, notes: 50, sources: 0, history: 0, question: 20 }),
        }),
    });
    view = await mount(<ChatView {...base} chatId={5} />);
    expect(view.container.querySelector(".context-summary")?.textContent).toContain("170");
    await click(buttonByText(view.container, "Details"));
    expect(rows().find((row) => row?.startsWith("Project notes"))).toBe("Project notes≈50");
    await view.unmount();
    mockFetch({
      "GET /api/chats/5": () =>
        json({
          ...chat,
          messages: [],
          context: saved({ instructions: 100, sources: 0, history: 0, question: 20 }),
        }),
    });
    view = await mount(<ChatView {...base} chatId={5} />);
    await click(buttonByText(view.container, "Details"));
    expect(rows().some((row) => row?.startsWith("Project notes"))).toBe(false);
  });

  it("shows the context meter and the local model's token counts", async () => {
    let saved = false;
    const answer = {
      id: 2,
      role: "assistant",
      content: "Hello!",
      provider: "local",
      model: "qwen",
      created_at: "t",
      sources: [],
    };
    mockFetch({
      "GET /api/chats/5": () =>
        json({ ...chat, messages: saved ? [{ ...answer, id: 1, role: "user", content: "hi" }, answer] : [] }),
      "POST /api/chats/5/messages": () => {
        saved = true;
        return sse([
          {
            type: "context",
            prompt_tokens: 900,
            context_tokens: 8192,
            reply_tokens: 1024,
            parts: { instructions: 100, sources: 0, history: 700, question: 100 },
            source_count: 0,
            history_kept: 3,
            history_total: 5,
          },
          { type: "token", text: "Hello!" },
          { type: "usage", prompt_tokens: 620, generated_tokens: 42, tokens_per_second: 18.5 },
          { type: "done", message_id: 2 },
        ]);
      },
    });
    view = await mount(<ChatView {...base} chatId={5} />);
    expect(view.container.querySelector('[role="meter"]')).toBeNull(); // nothing asked yet
    await typeInto(view.container.querySelector("textarea"), "hi");
    await click(view.container.querySelector('button[type="submit"]'));
    const summary = view.container.querySelector(".context-summary");
    expect(summary?.textContent).toContain("Context 662 / 8.2k tokens"); // the model's own count
    expect(summary?.textContent).toContain("2 older left out");
    expect(view.container.querySelector(".context-details")).toBeNull();
    await click(buttonByText(view.container, "Details"));
    const rows = [...view.container.querySelectorAll(".context-parts li")].map((li) => li.textContent);
    // The estimated parts (900) are scaled to the model's count of the prompt (620).
    expect(rows).toEqual([
      "Instructions≈69",
      "Earlier messages3 of 5 exchanges≈482",
      "Your question≈69",
      "Written by the model42",
      "Kept for the reply982",
      "Free6,548",
    ]);
    expect(view.container.querySelector(".context-foot")?.textContent).toContain(
      "3 of 5 earlier exchanges are in the model's memory; older ones are left out.",
    );
    expect(view.container.querySelector(".answer-meta")?.textContent).toBe(
      "qwen · local · 42 tokens · 18.5 tokens/s",
    );
  });

  it("asks a not-found question again in chat mode, and keeps a draft in the box", async () => {
    const question = {
      id: 1,
      role: "user",
      content: "notice period?",
      provider: null,
      model: null,
      created_at: "t",
      sources: [],
    };
    const notFound = { ...question, id: 2, role: "assistant", content: "Not found in the documents." };
    const calls = mockFetch({
      "GET /api/chats/5": () => json({ ...chat, messages: [question, notFound] }),
      "POST /api/chats/5/messages": () =>
        sse([
          { type: "token", text: "Often 30 days." },
          { type: "done", message_id: 3 },
        ]),
    });
    view = await mount(<ChatView {...base} chatId={5} collectionName="Contracts" />);
    await typeInto(view.container.querySelector("textarea"), "a draft");
    await click(buttonByText(view.container, "Ask the model anyway (no sources)"));
    const asked = calls.find((c) => c.key === "POST /api/chats/5/messages");
    expect(asked?.body).toEqual({ content: "notice period?", mode: "chat", think: false });
    expect(view.container.querySelector("textarea")?.value).toBe("a draft");
  });

  it("offers to ask again only on the last reply, when it is not found", async () => {
    const message = (id: number, role: string, content: string, sources: unknown[] = []) => ({
      id,
      role,
      content,
      provider: null,
      model: null,
      created_at: "t",
      sources,
    });
    mockFetch({
      "GET /api/chats/5": () =>
        json({
          ...chat,
          messages: [
            message(1, "user", "notice period?"),
            message(2, "assistant", "Not found in the documents."),
            message(3, "user", "How long?"),
            message(4, "assistant", "Three years [1].", [source]),
          ],
        }),
    });
    view = await mount(<ChatView {...base} chatId={5} />);
    expect(buttonByText(view.container, "Ask the model anyway (no sources)")).toBeUndefined();
  });

  it("says a chat-mode reply is not from the documents", async () => {
    mockFetch({
      "GET /api/chats/5": () =>
        json({
          ...chat,
          messages: [
            {
              id: 2,
              role: "assistant",
              content: "Paris is the capital of France.",
              provider: "local",
              model: "qwen",
              created_at: "t",
              sources: [],
            },
            {
              id: 4,
              role: "assistant",
              content: "Three years [1].",
              provider: "local",
              model: "qwen",
              created_at: "t",
              sources: [source],
            },
          ],
        }),
    });
    view = await mount(<ChatView {...base} chatId={5} />);
    const notes = view.container.querySelectorAll(".answer-general");
    expect(notes).toHaveLength(1); // the cited answer has none
    expect(notes[0].textContent).toContain("not from your documents");
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
