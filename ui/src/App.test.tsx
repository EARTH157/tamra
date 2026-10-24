import { act } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { SettingsProvider } from "./settings";
import { modelsInfo, saved } from "./test-models";
import type { Settings } from "./types";
import {
  buttonByText,
  click,
  json,
  type Mounted,
  mockFetch,
  mount,
  press,
  settle,
  typeInto,
} from "./test-utils";

let view: Mounted | undefined;

afterEach(async () => {
  await view?.unmount();
  view = undefined;
  vi.unstubAllGlobals();
  localStorage.clear();
});

const idle = {
  stale: false,
  counts: { pending: 0, indexing: 0, indexed: 2, failed: 0, skipped: 0 },
  current: null,
  error: null,
  problems: [],
};
const contracts = { id: 1, name: "Contracts", folder_path: "C:/c", notes: "", index: idle };
const lease = {
  id: 3,
  title: "Lease length",
  created_at: "t",
  updated_at: "t",
  project_id: 1,
  scope: [1],
};

function withChats(extra: Record<string, () => Response> = {}) {
  return mockFetch({
    "GET /api/projects": () => json({ projects: [contracts] }),
    "GET /api/chats": () => json({ chats: [lease] }),
    ...extra,
  });
}

function dialog(): HTMLElement | null {
  return document.querySelector('[role="dialog"]');
}

/** Fire a drag event carrying files (jsdom has no DataTransfer of its own). */
async function drag(target: Element | null, type: string, files: File[], types = ["Files"]) {
  if (!target) throw new Error("nothing to drag onto");
  const event = new Event(type, { bubbles: true, cancelable: true });
  Object.defineProperty(event, "dataTransfer", { value: { types, files, dropEffect: "none" } });
  await act(async () => {
    target.dispatchEvent(event);
  });
  await settle();
  return event;
}

/** Press Ctrl and a key, identified by its place on the keyboard (`code`) and what it types. */
async function ctrl(code: string, key: string) {
  const event = new KeyboardEvent("keydown", { code, key, ctrlKey: true, bubbles: true, cancelable: true });
  await act(async () => {
    document.body.dispatchEvent(event);
  });
  await settle();
  return event;
}

async function openMenu(container: HTMLElement) {
  await click(container.querySelector('button[aria-label="Options for Lease length"]'));
  return container.ownerDocument.querySelector('[role="menu"]');
}

describe("App", () => {
  it("welcomes a first run and asks for a project", async () => {
    mockFetch({
      "GET /api/projects": () => json({ projects: [] }),
      "GET /api/chats": () => json({ chats: [] }),
    });
    view = await mount(<App />);
    const text = view.container.textContent ?? "";
    expect(text).toContain("Welcome to Tamra");
    expect(text).toContain("Add your first project");
    expect(view.container.querySelector(".section-label")?.textContent).toBe("Projects");
    expect(text).toContain("No projects yet. Add a folder of documents to start.");
    expect(view.container.querySelector(".collection-card")).toBeNull(); // no project, no card
    expect(buttonByText(view.container, "New chat")?.disabled).toBe(true);
    expect(dialog()).toBeNull();
    await click(buttonByText(view.container, "Choose a folder"));
    expect(dialog()?.textContent).toContain("New project");
    expect(dialog()?.querySelector('input[aria-label="Folder path"]')).not.toBeNull();
  });

  it("shows the project, its chats and its index once a project exists", async () => {
    withChats();
    view = await mount(<App />);
    expect(view.container.textContent).toContain("2 files indexed");
    expect(view.container.textContent).toContain("Lease length");
    expect(view.container.textContent).toContain("Ask your documents");
  });

  it("adds files dropped on the window to the current project and says which were not added", async () => {
    const calls = withChats({
      "PUT /api/projects/1/files/lease%20notes.md": () => json({ name: "lease notes.md" }, 201),
      "PUT /api/projects/1/files/old.pdf": () =>
        json({ detail: "A file with this name is already in the project." }, 409),
    });
    view = await mount(<App />);
    const app = view.container.querySelector(".app");
    const files = [new File(["x"], "lease notes.md"), new File(["y"], "old.pdf"), new File(["z"], "sheet.xlsx")];
    await drag(app, "dragover", files);
    expect(document.querySelector(".drop-card")?.textContent).toBe("Drop to add to Contracts");
    await drag(app, "drop", files);
    expect(document.querySelector(".drop-card")).toBeNull();
    const puts = calls.filter((call) => call.key.startsWith("PUT"));
    expect(puts.map((call) => call.key)).toEqual([
      "PUT /api/projects/1/files/lease%20notes.md",
      "PUT /api/projects/1/files/old.pdf", // the spreadsheet is never sent
    ]);
    expect(puts[0].body).toBe(files[0]);
    expect(view.container.querySelector('[role="alert"]')?.textContent).toBe(
      "Not added: old.pdf (a file with this name is already in the project); " +
        "sheet.xlsx (only PDF, DOCX, TXT and MD files can be added)",
    );
  });

  it("searches the chats with Ctrl+K and opens the one chosen with Enter", async () => {
    const leave = { ...lease, id: 5, title: "Leave policy" };
    const calls = mockFetch({
      "GET /api/projects": () => json({ projects: [contracts] }),
      "GET /api/chats": () => json({ chats: [lease, leave] }),
      "GET /api/chats/search?q=rent": () =>
        json({ chats: [{ ...leave, match: "…the monthly rent is 18,500 baht…" }] }),
      "GET /api/chats/5": () => json({ ...leave, messages: [], context: null }),
    });
    view = await mount(<App />);
    const event = await ctrl("KeyK", "า"); // the K key on a Thai layout
    expect(event.defaultPrevented).toBe(true);
    expect(dialog()?.querySelector("h2")?.textContent).toBe("Search chats");
    const names = () => [...document.querySelectorAll(".chat-search-name")].map((el) => el.textContent);
    expect(names()).toEqual(["Lease length", "Leave policy"]); // recent first, before typing
    const input = document.querySelector<HTMLInputElement>(".chat-search-field input");
    await typeInto(input, "rent");
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 200)); // the search waits for a pause
    });
    await settle();
    expect(names()).toEqual(["Leave policy"]);
    expect(document.querySelector(".chat-search-match")?.textContent).toBe(
      "…the monthly rent is 18,500 baht…",
    );
    expect(document.querySelector(".chat-search-project")?.textContent).toBe("Contracts");
    await press(input, "Enter");
    expect(dialog()).toBeNull();
    expect(calls.map((call) => call.key)).toContain("GET /api/chats/5");
  });

  it("starts a new chat with Ctrl+N, and leaves plain keys alone", async () => {
    withChats({ "GET /api/chats/3": () => json({ ...lease, messages: [], context: null }) });
    view = await mount(<App />);
    await click(view.container.querySelector(".chat-title"));
    expect(view.container.querySelector(".chat-row.active")).not.toBeNull();
    expect((await ctrl("KeyN", "n")).defaultPrevented).toBe(true); // not a new window
    expect(view.container.querySelector(".chat-row.active")).toBeNull();
    expect(view.container.textContent).toContain("Ask your documents");
    const plain = new KeyboardEvent("keydown", { code: "KeyK", key: "k", bubbles: true, cancelable: true });
    document.body.dispatchEvent(plain);
    expect(plain.defaultPrevented).toBe(false);
    expect(dialog()).toBeNull();
  });

  it("leaves text dragged inside the app alone", async () => {
    withChats();
    view = await mount(<App />);
    const app = view.container.querySelector(".app");
    const event = await drag(app, "dragover", [], ["text/plain"]);
    expect(event.defaultPrevented).toBe(false);
    expect(document.querySelector(".drop-card")).toBeNull();
  });

  it("offers Rename, Move to project and Delete chat in the chat menu", async () => {
    withChats();
    view = await mount(<App />);
    const menu = await openMenu(view.container);
    const items = [...(menu?.querySelectorAll('[role="menuitem"]') ?? [])];
    expect(items.map((item) => item.textContent?.trim())).toEqual([
      "Rename",
      "Move to project",
      "Delete chat",
    ]);
    await press(menu, "Escape");
    expect(document.querySelector('[role="menu"]')).toBeNull();
  });

  it("renames a chat inline with Enter", async () => {
    const calls = withChats({
      "PATCH /api/chats/3": () => json({ ...lease, title: "Lease terms" }),
    });
    view = await mount(<App />);
    const menu = await openMenu(view.container);
    await click(buttonByText(menu!, "Rename"));
    const input = view.container.querySelector<HTMLInputElement>('input[aria-label="Chat title"]');
    expect(input?.value).toBe("Lease length");
    expect(view.container.textContent).toContain("Enter to save · Esc to cancel");
    await typeInto(input, "Lease terms");
    await press(input, "Enter");
    expect(calls).toContainEqual({ key: "PATCH /api/chats/3", body: { title: "Lease terms" } });
    expect(view.container.querySelector('input[aria-label="Chat title"]')).toBeNull();
    expect(view.container.textContent).toContain("Lease terms");
  });

  it("cancels a rename with Esc", async () => {
    const calls = withChats();
    view = await mount(<App />);
    const menu = await openMenu(view.container);
    await click(buttonByText(menu!, "Rename"));
    const input = view.container.querySelector('input[aria-label="Chat title"]');
    await typeInto(input, "Something else");
    await press(input, "Escape");
    expect(view.container.querySelector('input[aria-label="Chat title"]')).toBeNull();
    expect(view.container.textContent).toContain("Lease length");
    expect(calls.some((c) => c.key.startsWith("PATCH"))).toBe(false);
  });

  it("deletes a chat after confirming in the dialog", async () => {
    const calls = withChats({ "DELETE /api/chats/3": () => new Response(null, { status: 204 }) });
    view = await mount(<App />);
    let menu = await openMenu(view.container);
    await click(buttonByText(menu!, "Delete chat"));
    expect(dialog()?.textContent).toContain("Delete this chat?");
    expect(dialog()?.textContent).toContain('"Lease length" will be removed from this computer');
    await click(buttonByText(dialog()!, "Cancel"));
    expect(dialog()).toBeNull();
    expect(calls.some((c) => c.key.startsWith("DELETE"))).toBe(false);

    menu = await openMenu(view.container);
    await click(buttonByText(menu!, "Delete chat"));
    await click(buttonByText(dialog()!, "Delete chat"));
    expect(calls.map((c) => c.key)).toContain("DELETE /api/chats/3");
    expect(dialog()).toBeNull();
    expect(view.container.textContent).not.toContain("Lease length");
  });

  it("changes the folder in a dialog and shows why a folder was refused", async () => {
    let refuse = true;
    let project = contracts;
    const calls = withChats({
      "GET /api/projects": () => json({ projects: [project] }),
      "PUT /api/projects/1/folder": () => {
        if (refuse) return json({ detail: "Folder not found." }, 400);
        project = { ...contracts, folder_path: "D:/l" };
        return json(project);
      },
    });
    view = await mount(<App />);
    await click(buttonByText(view.container, "Change folder"));
    expect(dialog()?.textContent).toContain("Change documents folder");
    const input = dialog()?.querySelector('input[aria-label="Folder path"]');
    expect((input as HTMLInputElement).value).toBe("C:/c");
    await typeInto(input, "D:/nope");
    await click(buttonByText(dialog()!, "Use this folder"));
    expect(dialog()?.querySelector('[role="alert"]')?.textContent).toBe(
      "That folder does not exist.",
    );
    refuse = false;
    await typeInto(dialog()?.querySelector('input[aria-label="Folder path"]'), "D:/l");
    await click(buttonByText(dialog()!, "Use this folder"));
    expect(calls).toContainEqual({
      key: "PUT /api/projects/1/folder",
      body: { folder_path: "D:/l" },
    });
    expect(dialog()).toBeNull();
    expect(view.container.querySelector(".collection-path")?.textContent).toBe("D:/l");
  });

  it("shows a banner while the core is unreachable and retries on demand", async () => {
    let reachable = false;
    const calls = mockFetch({
      "GET /api/projects": () => {
        if (!reachable) throw new TypeError("Failed to fetch");
        return json({ projects: [contracts] });
      },
      "GET /api/chats": () => json({ chats: [lease] }),
    });
    view = await mount(<App />);
    const banner = view.container.querySelector('[role="alert"]');
    expect(banner?.textContent).toContain(
      "Tamra core is unreachable. Answers are paused until it reconnects.",
    );
    reachable = true;
    const count = (key: string) => calls.filter((c) => c.key === key).length;
    const polls = count("GET /api/projects");
    const chatLoads = count("GET /api/chats");
    await click(buttonByText(view.container, "Retry"));
    expect(count("GET /api/projects")).toBeGreaterThan(polls);
    expect(count("GET /api/chats")).toBeGreaterThan(chatLoads); // reloaded after reconnecting
    expect(view.container.textContent).not.toContain("Tamra core is unreachable");
    expect(view.container.textContent).toContain("2 files indexed");
  });

  it("closes the delete dialog with Esc wherever focus is", async () => {
    const calls = withChats();
    view = await mount(<App />);
    const menu = await openMenu(view.container);
    await click(buttonByText(menu!, "Delete chat"));
    expect(dialog()).not.toBeNull();
    (document.activeElement as HTMLElement | null)?.blur();
    await press(document.body, "Escape");
    expect(dialog()).toBeNull();
    expect(calls.some((c) => c.key.startsWith("DELETE"))).toBe(false);
  });

  it("keeps the chat menu on screen near the bottom of the window", async () => {
    withChats();
    vi.stubGlobal("innerHeight", 300);
    vi.stubGlobal("innerWidth", 400);
    const rect = (top: number, left: number, width: number, height: number) =>
      ({ top, left, width, height, bottom: top + height, right: left + width, x: left, y: top }) as DOMRect;
    const spy = vi
      .spyOn(Element.prototype, "getBoundingClientRect")
      .mockImplementation(function (this: Element) {
        if (this.getAttribute("role") === "menu") return rect(0, 0, 218, 90);
        return rect(266, 211, 28, 28); // the ⋯ button, 6 px above the window's bottom
      });
    try {
      view = await mount(<App />);
      const menu = (await openMenu(view.container)) as HTMLElement | null;
      const top = parseFloat(menu?.style.top ?? "NaN");
      const left = parseFloat(menu?.style.left ?? "NaN");
      expect(top + 90).toBeLessThanOrEqual(300 - 8); // fits above the bottom edge
      expect(top).toBe(266 - 3 - 90); // flipped above the ⋯ button
      expect(left + 218).toBeLessThanOrEqual(400 - 8);
    } finally {
      spy.mockRestore();
    }
  });
});

describe("Settings in the app", () => {
  /** The app inside the settings provider; the settings and models routes are answered. */
  async function mountWithSettings(
    settings: Partial<Settings> = {},
    extra: Record<string, () => Response> = {},
  ) {
    const calls = withChats({
      "GET /api/settings": () => json(saved(settings)),
      "GET /api/models": () => json(modelsInfo()),
      ...extra,
    });
    view = await mount(
      <SettingsProvider>
        <App />
      </SettingsProvider>,
    );
    return calls;
  }

  const settingsButton = () => buttonByText(view!.container, "Settings");

  it("opens the Settings page from the sidebar and returns to the chat from it", async () => {
    await mountWithSettings();
    expect(view!.container.querySelector(".settings")).toBeNull();
    await click(settingsButton());
    expect(view!.container.querySelector(".settings h1")?.textContent).toBe("Settings");
    expect(settingsButton()?.getAttribute("aria-current")).toBe("page");
    expect(view!.container.textContent).toContain("Interface language");
    await click(view!.container.querySelector('button[title="Lease length"]'));
    expect(view!.container.querySelector(".settings")).toBeNull();
    expect(view!.container.querySelector(".chat-view")).not.toBeNull();
    await click(settingsButton());
    await click(buttonByText(view!.container, "New chat"));
    expect(view!.container.querySelector(".settings")).toBeNull();
  });

  it("keeps the chat, with its unsent question, while Settings is open", async () => {
    await mountWithSettings();
    const box = () => view!.container.querySelector("textarea");
    await typeInto(box(), "How long is the lease?");
    await click(settingsButton());
    expect(view!.container.querySelector(".view-slot")?.hasAttribute("hidden")).toBe(true);
    await click(buttonByText(view!.container, "New chat"));
    expect((box() as HTMLTextAreaElement).value).toBe("How long is the lease?");
  });

  it("lets you return to the chat from Settings while an answer is streaming", async () => {
    const never = new ReadableStream<Uint8Array>({ start() {} }); // an answer that never ends
    await mountWithSettings(
      {},
      {
        "POST /api/chats": () => json(lease),
        "GET /api/chats/3": () => json({ ...lease, messages: [] }),
        "POST /api/chats/3/messages": () =>
          new Response(never, { headers: { "Content-Type": "text/event-stream" } }),
      },
    );
    await typeInto(view!.container.querySelector("textarea"), "How long is the lease?");
    await click(view!.container.querySelector('button[type="submit"]'));
    expect(buttonByText(view!.container, "New chat")?.disabled).toBe(true); // busy
    await click(settingsButton());
    expect(view!.container.querySelector(".settings")).not.toBeNull();
    expect(buttonByText(view!.container, "New chat")?.disabled).toBe(true);
    await click(settingsButton()); // the same button goes back
    expect(view!.container.querySelector(".settings")).toBeNull();
    expect(view!.container.querySelector(".view-slot")?.hasAttribute("hidden")).toBe(false);
    expect(view!.container.querySelector(".chat-view")).not.toBeNull();
    expect(settingsButton()?.hasAttribute("aria-current")).toBe(false);
  });

  it("reloads the models when the chat shows again, so the model chip is current", async () => {
    let models = modelsInfo();
    await mountWithSettings({}, { "GET /api/models": () => json(models) });
    const chip = () => view!.container.querySelector('button[aria-label^="Model"]');
    expect(chip()?.getAttribute("aria-label")).toBe("Model: Qwen3-4B");
    await click(settingsButton());
    // A model is imported and chosen in Settings meanwhile.
    models = modelsInfo({ active: { mode: "local", label: "mine", id: "import:mine.gguf" } });
    await click(settingsButton());
    expect(chip()?.getAttribute("aria-label")).toBe("Model: mine");
  });

  it("opens the AI model tab from Manage models in the chat", async () => {
    await mountWithSettings();
    await click(view!.container.querySelector('button[aria-label^="Model"]'));
    await click(buttonByText(document.body, "Manage models…"));
    expect(view!.container.querySelector(".settings")).not.toBeNull();
    expect(
      view!.container.querySelector('[role="tab"][aria-selected="true"]')?.textContent,
    ).toBe("AI model");
  });

  it("shows that AI apps can search while access is on, and opens General from it", async () => {
    await mountWithSettings({ mcp_enabled: true });
    await click(buttonByText(view!.container, "AI apps can search"));
    expect(view!.container.querySelector(".settings")).not.toBeNull();
    expect(
      view!.container.querySelector('[role="tab"][aria-selected="true"]')?.textContent,
    ).toBe("General");
  });

  it("shows no AI apps badge while access is off", async () => {
    await mountWithSettings();
    expect(buttonByText(view!.container, "AI apps can search")).toBeUndefined();
  });

  it("deletes a chat without asking when the setting is off", async () => {
    const calls = await mountWithSettings(
      { ask_before_delete: false },
      { "DELETE /api/chats/3": () => new Response(null, { status: 204 }) },
    );
    const menu = await openMenu(view!.container);
    await click(buttonByText(menu!, "Delete chat"));
    expect(dialog()).toBeNull();
    expect(calls.map((c) => c.key)).toContain("DELETE /api/chats/3");
    expect(view!.container.textContent).not.toContain("Lease length");
  });

  it("still asks before deleting a chat when the setting is on", async () => {
    const calls = await mountWithSettings({ ask_before_delete: true });
    const menu = await openMenu(view!.container);
    await click(buttonByText(menu!, "Delete chat"));
    expect(dialog()).not.toBeNull();
    expect(calls.some((c) => c.key.startsWith("DELETE"))).toBe(false);
  });

  it("reloads the chats and leaves the open chat after Delete all chats", async () => {
    let remaining = [lease];
    const calls = await mountWithSettings(
      {},
      {
        "GET /api/chats": () => json({ chats: remaining }),
        "GET /api/chats/3": () => json({ ...lease, messages: [] }),
        "DELETE /api/chats": () => {
          remaining = [];
          return new Response(null, { status: 204 });
        },
      },
    );
    await click(view!.container.querySelector('button[title="Lease length"]'));
    expect(view!.container.querySelector(".chat-row.active")).not.toBeNull();
    await click(settingsButton());
    expect(view!.container.textContent).toContain("1 chat saved on this computer");
    await click(buttonByText(view!.container, "Delete all chats"));
    await click(buttonByText(dialog()!, "Delete all chats"));
    expect(calls.map((c) => c.key)).toContain("DELETE /api/chats");
    expect(view!.container.textContent).toContain("No chats saved yet");
    expect(view!.container.querySelector(".chat-row")).toBeNull();
    expect(view!.container.textContent).toContain("No chats yet. Ask a question to start one.");
    await click(buttonByText(view!.container, "New chat"));
    expect(view!.container.textContent).toContain("Ask your documents"); // an empty chat, not Lease length
  });

  it("has no folder or index rows: they belong to each project, in Project settings", async () => {
    await mountWithSettings();
    await click(settingsButton());
    expect(view!.container.textContent).not.toContain("Search index");
    expect(view!.container.textContent).not.toContain("Documents folder");
  });
});
