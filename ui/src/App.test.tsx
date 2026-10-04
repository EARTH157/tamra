import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import {
  buttonByText,
  click,
  json,
  type Mounted,
  mockFetch,
  mount,
  press,
  typeInto,
} from "./test-utils";

let view: Mounted | undefined;

afterEach(async () => {
  await view?.unmount();
  view = undefined;
  vi.unstubAllGlobals();
});

const collectionState = {
  collection: { id: 1, name: "Contracts", folder_path: "C:/c", created_at: "t" },
  index: {
    stale: false,
    counts: { pending: 0, indexing: 0, indexed: 2, failed: 0, skipped: 0 },
    current: null,
    error: null,
    problems: [],
  },
};
const lease = { id: 3, title: "Lease length", created_at: "t", updated_at: "t" };

function withChats(extra: Record<string, () => Response> = {}) {
  return mockFetch({
    "GET /api/collection": () => json(collectionState),
    "GET /api/chats": () => json({ chats: [lease] }),
    ...extra,
  });
}

function dialog(): HTMLElement | null {
  return document.querySelector('[role="dialog"]');
}

async function openMenu(container: HTMLElement) {
  await click(container.querySelector('button[aria-label="Options for Lease length"]'));
  return container.ownerDocument.querySelector('[role="menu"]');
}

describe("App", () => {
  it("welcomes a first run and asks for a folder", async () => {
    mockFetch({
      "GET /api/collection": () => json({ collection: null, index: null }),
      "GET /api/chats": () => json({ chats: [] }),
    });
    view = await mount(<App />);
    const text = view.container.textContent ?? "";
    expect(text).toContain("Welcome to Tamra");
    expect(text).toContain("Add a folder of documents");
    expect(view.container.querySelector(".section-label")?.textContent).toBe("Chats");
    expect(text).toContain("No chats yet. Add a folder of documents to start.");
    expect(buttonByText(view.container, "New chat")?.disabled).toBe(true);
    expect(dialog()).toBeNull();
    await click(buttonByText(view.container, "Choose a folder"));
    expect(dialog()?.querySelector('input[aria-label="Folder path"]')).not.toBeNull();
  });

  it("shows the index and the chats once a folder is chosen", async () => {
    withChats();
    view = await mount(<App />);
    expect(view.container.textContent).toContain("2 files indexed");
    expect(view.container.textContent).toContain("Lease length");
    expect(view.container.textContent).toContain("Ask your documents");
  });

  it("offers only Rename and Delete chat in the chat menu", async () => {
    withChats();
    view = await mount(<App />);
    const menu = await openMenu(view.container);
    const items = [...(menu?.querySelectorAll('[role="menuitem"]') ?? [])];
    expect(items.map((item) => item.textContent?.trim())).toEqual(["Rename", "Delete chat"]);
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

  it("changes the folder in a dialog and shows the server's error", async () => {
    let refuse = true;
    const calls = withChats({
      "PUT /api/collection": () =>
        refuse
          ? json({ detail: "Folder not found: D:/nope" }, 400)
          : json({
              ...collectionState,
              collection: { ...collectionState.collection, name: "Leases", folder_path: "D:/l" },
            }),
    });
    view = await mount(<App />);
    await click(buttonByText(view.container, "Change folder"));
    expect(dialog()?.textContent).toContain("Change documents folder");
    const input = dialog()?.querySelector('input[aria-label="Folder path"]');
    expect((input as HTMLInputElement).value).toBe("C:/c");
    await typeInto(input, "D:/nope");
    await click(buttonByText(dialog()!, "Use this folder"));
    expect(dialog()?.querySelector('[role="alert"]')?.textContent).toBe(
      "Folder not found: D:/nope",
    );
    refuse = false;
    await typeInto(dialog()?.querySelector('input[aria-label="Folder path"]'), "D:/l");
    await click(buttonByText(dialog()!, "Use this folder"));
    expect(calls).toContainEqual({
      key: "PUT /api/collection",
      body: { name: "", folder_path: "D:/l" },
    });
    expect(dialog()).toBeNull();
    expect(view.container.textContent).toContain("Leases");
  });

  it("shows a banner while the core is unreachable and retries on demand", async () => {
    let reachable = false;
    const calls = mockFetch({
      "GET /api/collection": () => {
        if (!reachable) throw new TypeError("Failed to fetch");
        return json(collectionState);
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
    const polls = count("GET /api/collection");
    const chatLoads = count("GET /api/chats");
    await click(buttonByText(view.container, "Retry"));
    expect(count("GET /api/collection")).toBeGreaterThan(polls);
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
