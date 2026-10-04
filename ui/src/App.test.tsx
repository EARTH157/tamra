import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { json, type Mounted, mockFetch, mount } from "./test-utils";

let view: Mounted | undefined;

afterEach(async () => {
  await view?.unmount();
  view = undefined;
  vi.unstubAllGlobals();
});

describe("App", () => {
  it("asks for a folder first", async () => {
    mockFetch({
      "GET /api/collection": () => json({ collection: null, index: null }),
      "GET /api/chats": () => json({ chats: [] }),
    });
    view = await mount(<App />);
    expect(view.container.textContent).toContain("Choose a folder of documents");
  });

  it("shows the index and the chats once a folder is chosen", async () => {
    mockFetch({
      "GET /api/collection": () =>
        json({
          collection: { id: 1, name: "Contracts", folder_path: "C:/c", created_at: "t" },
          index: {
            stale: false,
            counts: { pending: 0, indexing: 0, indexed: 2, failed: 0, skipped: 0 },
            current: null,
            error: null,
            problems: [],
          },
        }),
      "GET /api/chats": () =>
        json({ chats: [{ id: 3, title: "Lease length", created_at: "t", updated_at: "t" }] }),
    });
    view = await mount(<App />);
    expect(view.container.textContent).toContain("2 of 2 files indexed");
    expect(view.container.textContent).toContain("Lease length");
  });
});
