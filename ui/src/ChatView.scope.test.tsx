import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import ChatView from "./ChatView";
import { click, json, type Mounted, mockFetch, mount, press, settle, sse, typeInto } from "./test-utils";
import type { Chat, Project } from "./types";

let view: Mounted | undefined;

afterEach(async () => {
  await view?.unmount();
  view = undefined;
  vi.unstubAllGlobals();
});

const idle = {
  stale: false,
  counts: { pending: 0, indexing: 0, indexed: 2, failed: 0, skipped: 0 },
  current: null,
  error: null,
  problems: [],
};
const project = (id: number, name: string): Project => ({
  id,
  name,
  folder_path: `C:/${name}`,
  index: idle,
});
const hr = project(1, "HR Documents");
const contracts = project(2, "Contracts");
const manuals = project(3, "Manuals");
const projects = [contracts, hr, manuals]; // name order, as the core lists them

const chatOf = (id: number, home: number, scope: number[]): Chat => ({
  id,
  title: "",
  created_at: "t",
  updated_at: "t",
  project_id: home,
  scope,
});

const base = {
  createChat: vi.fn(),
  onBusyChange: vi.fn(),
  onAnswered: vi.fn(),
  onOpenSettings: vi.fn(),
  projects,
};

function chip(): HTMLButtonElement {
  const found = view?.container.querySelector<HTMLButtonElement>('button[aria-label^="Search in"]');
  if (!found) throw new Error("no Search in chip");
  return found;
}

function menu(): HTMLElement | null {
  return view?.container.querySelector<HTMLElement>('[role="menu"][aria-label="Search in"]') ?? null;
}

function items(): HTMLButtonElement[] {
  return [...(menu()?.querySelectorAll<HTMLButtonElement>('[role="menuitemcheckbox"]') ?? [])];
}

function item(name: string): HTMLButtonElement {
  const found = items().find((el) => el.querySelector(".entry-name")?.textContent === name);
  if (!found) throw new Error(`no menu item ${name}`);
  return found;
}

async function ask(text = "How long?") {
  await typeInto(view?.container.querySelector("textarea"), text);
  await click(view?.container.querySelector('button[type="submit"]'));
}

describe("Search in chip", () => {
  it("shows the home project's name when the scope is that project", async () => {
    mockFetch({ "GET /api/chats/5": () => json({ ...chatOf(5, 1, [1]), messages: [] }) });
    view = await mount(<ChatView {...base} chatId={5} />);
    expect(chip().getAttribute("aria-label")).toBe("Search in: HR Documents");
    expect(chip().textContent).toBe("HR Documents");
  });

  it("shows the home project's name and how many more are searched", async () => {
    mockFetch({ "GET /api/chats/5": () => json({ ...chatOf(5, 1, [1, 2, 3]), messages: [] }) });
    view = await mount(<ChatView {...base} chatId={5} />);
    expect(chip().textContent).toBe("HR Documents + 2");
  });

  it("names the first searched project when the home project is not searched", async () => {
    mockFetch({ "GET /api/chats/5": () => json({ ...chatOf(5, 1, [3]), messages: [] }) });
    view = await mount(<ChatView {...base} chatId={5} />);
    expect(chip().textContent).toBe("Manuals");
  });

  it("lists the home project first, marked home, with a checkbox per project", async () => {
    mockFetch({ "GET /api/chats/5": () => json({ ...chatOf(5, 1, [1, 3]), messages: [] }) });
    view = await mount(<ChatView {...base} chatId={5} />);
    await click(chip());
    expect(items().map((el) => el.querySelector(".entry-name")?.textContent)).toEqual([
      "HR Documents",
      "Contracts",
      "Manuals",
    ]);
    expect(items().map((el) => el.getAttribute("aria-checked"))).toEqual(["true", "false", "true"]);
    expect(items()[0].textContent).toContain("home");
    expect(items()[1].textContent).not.toContain("home");
  });

  it("saves a ticked project at once and keeps the menu open", async () => {
    const onChatChanged = vi.fn();
    const calls = mockFetch({
      "GET /api/chats/5": () => json({ ...chatOf(5, 1, [1]), messages: [] }),
      "PUT /api/chats/5/scope": () => json(chatOf(5, 1, [1, 2])),
    });
    view = await mount(<ChatView {...base} chatId={5} onChatChanged={onChatChanged} />);
    await click(chip());
    await click(item("Contracts"));
    expect(calls.filter((c) => c.key === "PUT /api/chats/5/scope")).toEqual([
      { key: "PUT /api/chats/5/scope", body: { project_ids: [1, 2] } },
    ]);
    expect(onChatChanged).toHaveBeenCalledWith(chatOf(5, 1, [1, 2]));
    expect(menu()).not.toBeNull();
    expect(item("Contracts").getAttribute("aria-checked")).toBe("true");
    expect(chip().textContent).toBe("HR Documents + 1");
  });

  it("saves an unticked project", async () => {
    const calls = mockFetch({
      "GET /api/chats/5": () => json({ ...chatOf(5, 1, [1, 2]), messages: [] }),
      "PUT /api/chats/5/scope": () => json(chatOf(5, 1, [1])),
    });
    view = await mount(<ChatView {...base} chatId={5} />);
    await click(chip());
    await click(item("Contracts"));
    expect(calls.find((c) => c.key === "PUT /api/chats/5/scope")?.body).toEqual({ project_ids: [1] });
    expect(chip().textContent).toBe("HR Documents");
  });

  it("does not let the last ticked project be unticked", async () => {
    const calls = mockFetch({ "GET /api/chats/5": () => json({ ...chatOf(5, 1, [1]), messages: [] }) });
    view = await mount(<ChatView {...base} chatId={5} />);
    await click(chip());
    expect(item("HR Documents").getAttribute("aria-disabled")).toBe("true");
    expect(item("Contracts").getAttribute("aria-disabled")).toBeNull();
    await click(item("HR Documents"));
    expect(calls.some((c) => c.key.startsWith("PUT"))).toBe(false);
    expect(item("HR Documents").getAttribute("aria-checked")).toBe("true");
  });

  it("moves with the arrow keys and closes with Esc", async () => {
    const calls = mockFetch({
      "GET /api/chats/5": () => json({ ...chatOf(5, 1, [1]), messages: [] }),
      "PUT /api/chats/5/scope": () => json(chatOf(5, 1, [1, 2])),
    });
    view = await mount(<ChatView {...base} chatId={5} />);
    await click(chip());
    expect(document.activeElement).toBe(item("HR Documents"));
    await press(menu(), "ArrowDown");
    expect(document.activeElement).toBe(item("Contracts"));
    await click(document.activeElement as HTMLElement);
    expect(calls.some((c) => c.key === "PUT /api/chats/5/scope")).toBe(true);
    await press(document.activeElement, "Escape");
    expect(menu()).toBeNull();
    expect(document.activeElement).toBe(chip());
  });

  it("reverts and says why when the core refuses the scope", async () => {
    mockFetch({
      "GET /api/chats/5": () => json({ ...chatOf(5, 1, [1]), messages: [] }),
      "PUT /api/chats/5/scope": () => json({ detail: "Chat not found." }, 404),
    });
    view = await mount(<ChatView {...base} chatId={5} />);
    await click(chip());
    await click(item("Contracts"));
    expect(chip().textContent).toBe("HR Documents");
    expect(view.container.querySelector('[role="alert"]')?.textContent).toBe(
      "This chat no longer exists.",
    );
  });

  it("follows the open chat when the app's chat changes (a move or a deleted project)", async () => {
    mockFetch({ "GET /api/chats/5": () => json({ ...chatOf(5, 1, [1, 2]), messages: [] }) });
    function Host() {
      const [chat, setChat] = useState(chatOf(5, 1, [1, 2]));
      return (
        <>
          <button type="button" id="move" onClick={() => setChat(chatOf(5, 3, [3]))} />
          <ChatView {...base} chatId={5} chat={chat} />
        </>
      );
    }
    view = await mount(<Host />);
    expect(chip().textContent).toBe("HR Documents + 1");
    await click(view.container.querySelector("#move"));
    expect(chip().textContent).toBe("Manuals");
  });

  it("falls back to the home project when the scope names only deleted projects", async () => {
    mockFetch({ "GET /api/chats/5": () => json({ ...chatOf(5, 1, [9]), messages: [] }) });
    view = await mount(<ChatView {...base} chatId={5} />);
    expect(chip().textContent).toBe("HR Documents");
  });

  it("is disabled while an answer is being written", async () => {
    let finish: () => void = () => {};
    const open = new ReadableStream<Uint8Array>({
      start(controller) {
        finish = () => controller.close();
      },
    });
    mockFetch({
      "GET /api/chats/5": () => json({ ...chatOf(5, 1, [1]), messages: [] }),
      "POST /api/chats/5/messages": () =>
        new Response(open, { headers: { "Content-Type": "text/event-stream" } }),
    });
    view = await mount(<ChatView {...base} chatId={5} />);
    await ask();
    expect(chip().disabled).toBe(true);
    finish();
    await settle();
    expect(chip().disabled).toBe(false);
  });

  it("shows no chip without projects", async () => {
    mockFetch({ "GET /api/chats/5": () => json({ ...chatOf(5, 1, [1]), messages: [] }) });
    view = await mount(<ChatView {...base} projects={undefined} chatId={5} />);
    expect(view.container.querySelector('button[aria-label^="Search in"]')).toBeNull();
  });

  it("words a refused chat load in the user's language", async () => {
    mockFetch({ "GET /api/chats/5": () => json({ detail: "Chat not found." }, 404) });
    view = await mount(<ChatView {...base} chatId={5} />);
    expect(view.container.querySelector('[role="alert"]')?.textContent).toBe(
      "This chat no longer exists.",
    );
  });
});

describe("Search in chip on a chat that does not exist yet", () => {
  it("starts with the default project and saves nothing when it is left alone", async () => {
    const calls = mockFetch({
      "POST /api/chats/9/messages": () => sse([{ type: "done", message_id: 1 }]),
      "GET /api/chats/9": () => json({ ...chatOf(9, 1, [1]), messages: [] }),
    });
    const createChat = vi.fn(async () => 9);
    view = await mount(
      <ChatView {...base} chatId={null} createChat={createChat} defaultProjectId={1} />,
    );
    expect(chip().textContent).toBe("HR Documents");
    await ask();
    expect(calls.some((c) => c.key.startsWith("PUT"))).toBe(false);
  });

  it("keeps the choice locally, then saves it after the chat is created and before the question", async () => {
    const calls = mockFetch({
      "PUT /api/chats/9/scope": () => json(chatOf(9, 1, [1, 3])),
      "POST /api/chats/9/messages": () => sse([{ type: "done", message_id: 1 }]),
      "GET /api/chats/9": () => json({ ...chatOf(9, 1, [1, 3]), messages: [] }),
    });
    const createChat = vi.fn(async () => 9);
    const onChatChanged = vi.fn();
    view = await mount(
      <ChatView
        {...base}
        chatId={null}
        createChat={createChat}
        defaultProjectId={1}
        onChatChanged={onChatChanged}
      />,
    );
    await click(chip());
    await click(item("Manuals"));
    expect(chip().textContent).toBe("HR Documents + 1");
    expect(calls.some((c) => c.key.startsWith("PUT"))).toBe(false); // no chat to save it on yet
    await ask();
    expect(createChat).toHaveBeenCalledTimes(1);
    expect(
      calls.map((c) => c.key).filter((key) => key !== "GET /api/models" && key !== "GET /api/settings"),
    ).toEqual(["PUT /api/chats/9/scope", "POST /api/chats/9/messages", "GET /api/chats/9"]);
    expect(calls.find((c) => c.key === "PUT /api/chats/9/scope")?.body).toEqual({
      project_ids: [1, 3],
    });
    expect(onChatChanged).toHaveBeenCalledWith(chatOf(9, 1, [1, 3]));
  });

  it("does not ask when the scope cannot be saved", async () => {
    const calls = mockFetch({
      "PUT /api/chats/9/scope": () => json({ detail: "Unknown project." }, 400),
      "GET /api/chats/9": () => json({ ...chatOf(9, 1, [1]), messages: [] }),
    });
    view = await mount(
      <ChatView {...base} chatId={null} createChat={async () => 9} defaultProjectId={1} />,
    );
    await click(chip());
    await click(item("Manuals"));
    await ask();
    expect(calls.some((c) => c.key === "POST /api/chats/9/messages")).toBe(false);
    expect(view.container.querySelector('[role="alert"]')?.textContent).toBe("Unknown project.");
  });

  it("goes back to the default project for the next new chat", async () => {
    mockFetch({});
    view = await mount(
      <ChatView {...base} chatId={null} createChat={async () => 9} defaultProjectId={1} />,
    );
    await click(chip());
    await click(item("Manuals"));
    expect(chip().textContent).toBe("HR Documents + 1");
    await view.unmount();
    view = await mount(
      <ChatView {...base} chatId={null} createChat={async () => 9} defaultProjectId={1} />,
    );
    expect(chip().textContent).toBe("HR Documents");
  });
});

const message = (id: number, role: "user" | "assistant", content: string, sources: unknown[] = []) => ({
  id,
  role,
  content,
  provider: null,
  model: null,
  created_at: "t",
  sources,
});
const source = (n: number, file: string, projectName: string | null) => ({
  n,
  file_id: n,
  file,
  label: "p. 2",
  text: `Passage ${n}.`,
  location: { kind: "pdf" },
  project: projectName,
});

describe("project labels on sources", () => {
  async function mountAnswer(sources: unknown[]) {
    mockFetch({
      "GET /api/chats/5": () =>
        json({
          ...chatOf(5, 1, [1, 2]),
          messages: [message(1, "user", "Q?"), message(2, "assistant", "A [1] [2].", sources)],
        }),
    });
    view = await mount(<ChatView {...base} chatId={5} />);
  }
  const cards = () =>
    [...(view?.container.querySelectorAll(".source-card .source-file") ?? [])].map(
      (el) => el.textContent,
    );

  it("names the project on every source when the answer spans projects", async () => {
    await mountAnswer([source(1, "a/lease.pdf", "HR Documents"), source(2, "nda.pdf", "Contracts")]);
    expect(cards()).toEqual(["HR Documents · lease.pdf", "Contracts · nda.pdf"]);
  });

  it("names no project when the answer stays in one project", async () => {
    await mountAnswer([source(1, "lease.pdf", "HR Documents"), source(2, "nda.pdf", "HR Documents")]);
    expect(cards()).toEqual(["lease.pdf", "nda.pdf"]);
  });

  it("names no project on sources whose project is unknown", async () => {
    await mountAnswer([source(1, "lease.pdf", null), source(2, "nda.pdf", null)]);
    expect(cards()).toEqual(["lease.pdf", "nda.pdf"]);
  });

  it("shows the project in the source panel title when the answer spans projects", async () => {
    await mountAnswer([source(1, "lease.pdf", "HR Documents"), source(2, "nda.pdf", "Contracts")]);
    await click(view?.container.querySelectorAll(".source-card")[1]);
    expect(view?.container.querySelector(".source-panel header strong")?.textContent).toBe(
      "Contracts · nda.pdf",
    );
  });

  it("shows no project in the panel title for a one-project answer", async () => {
    await mountAnswer([source(1, "lease.pdf", "HR Documents")]);
    await click(view?.container.querySelector(".source-card"));
    expect(view?.container.querySelector(".source-panel header strong")?.textContent).toBe(
      "lease.pdf",
    );
  });

  it("labels the passages of a search-only answer", async () => {
    mockFetch({
      "GET /api/chats/5": () =>
        json({
          ...chatOf(5, 1, [1, 2]),
          messages: [
            message(1, "user", "Q?"),
            message(2, "assistant", "", [
              source(1, "lease.pdf", "HR Documents"),
              source(2, "nda.pdf", "Contracts"),
            ]),
          ],
        }),
    });
    view = await mount(<ChatView {...base} chatId={5} />);
    expect(
      [...view.container.querySelectorAll(".passage-file")].map((el) => el.textContent),
    ).toEqual(["HR Documents · lease.pdf", "Contracts · nda.pdf"]);
  });

  it("labels a source while its answer is still streaming", async () => {
    const encoder = new TextEncoder();
    let push: (event: unknown) => void = () => {};
    let finish: () => void = () => {};
    const open = new ReadableStream<Uint8Array>({
      start(controller) {
        push = (event) => controller.enqueue(encoder.encode(`data: ${JSON.stringify(event)}

`));
        finish = () => controller.close();
      },
    });
    mockFetch({
      "GET /api/chats/5": () => json({ ...chatOf(5, 1, [1, 2]), messages: [] }),
      "POST /api/chats/5/messages": () =>
        new Response(open, { headers: { "Content-Type": "text/event-stream" } }),
    });
    view = await mount(<ChatView {...base} chatId={5} />);
    await ask();
    push({
      type: "sources",
      sources: [source(1, "lease.pdf", "HR Documents"), source(2, "nda.pdf", "Contracts")],
    });
    push({ type: "token", text: "See [1] and [2]." });
    await settle();
    expect(cards()).toEqual(["HR Documents · lease.pdf", "Contracts · nda.pdf"]);
    await click(view.container.querySelectorAll(".source-card")[1]);
    expect(view.container.querySelector(".source-panel header strong")?.textContent).toBe(
      "Contracts · nda.pdf",
    );
    finish();
    await settle();
  });
});

describe("stale project notice", () => {
  const stream = () =>
    sse([
      { type: "notice", code: "stale_projects", names: ["Contracts", "Manuals"] },
      { type: "sources", sources: [] },
      { type: "token", text: "Nothing here." },
      { type: "done", message_id: 3 },
    ]);

  it("tells which projects were skipped, and can be dismissed", async () => {
    mockFetch({
      "GET /api/chats/5": () => json({ ...chatOf(5, 1, [1, 2, 3]), messages: [] }),
      "POST /api/chats/5/messages": stream,
    });
    view = await mount(<ChatView {...base} chatId={5} />);
    expect(view.container.querySelector(".chat-notice")).toBeNull();
    await ask();
    const line = view.container.querySelector(".chat-notice");
    expect(line?.textContent).toContain("Skipped Contracts, Manuals: the index must be rebuilt.");
    await click(line?.querySelector("button"));
    expect(view.container.querySelector(".chat-notice")).toBeNull();
  });

  it("goes away when the next question is asked", async () => {
    let first = true;
    mockFetch({
      "GET /api/chats/5": () => json({ ...chatOf(5, 1, [1, 2, 3]), messages: [] }),
      "POST /api/chats/5/messages": () => {
        if (first) {
          first = false;
          return stream();
        }
        return sse([
          { type: "sources", sources: [] },
          { type: "done", message_id: 4 },
        ]);
      },
    });
    view = await mount(<ChatView {...base} chatId={5} />);
    await ask();
    expect(view.container.querySelector(".chat-notice")).not.toBeNull();
    await ask("again");
    expect(view.container.querySelector(".chat-notice")).toBeNull();
  });
});
