import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { click, json, type Mounted, mount, settle, sse, typeInto } from "./test-utils";
import type { Chat, Project } from "./types";

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
const projects: Project[] = [
  { id: 1, name: "Contracts", folder_path: "C:/c", index: idle },
  { id: 2, name: "Eval corpus", folder_path: "C:/e", index: idle },
];
const chat = (scope: number[]): Chat => ({
  id: 3,
  title: "Lease length",
  created_at: "t",
  updated_at: "t",
  project_id: 1,
  scope,
});

describe("The chat list and the Search in chip", () => {
  it("ignores a chat list that was asked for before the scope was changed", async () => {
    let listCalls = 0;
    let release: (response: Response) => void = () => {};
    const late = new Promise<Response>((resolve) => {
      release = resolve;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const key = `${init?.method ?? "GET"} ${String(input)}`;
        if (key === "GET /api/projects") return json({ projects });
        if (key === "GET /api/chats") {
          listCalls++;
          if (listCalls === 1) return json({ chats: [chat([1])] });
          if (listCalls === 2) return late; // the reload after the answer, answered slowly
          return json({ chats: [chat([1, 2])] });
        }
        if (key === "GET /api/chats/3") return json({ ...chat([1]), messages: [] });
        if (key === "POST /api/chats/3/messages") {
          return sse([{ type: "done", message_id: 1 }]);
        }
        if (key === "PUT /api/chats/3/scope") return json(chat([1, 2]));
        throw new Error(`unexpected request: ${key}`);
      }),
    );
    view = await mount(<App />);
    await click(view.container.querySelector(".chat-title"));
    await typeInto(view.container.querySelector("textarea"), "How long?");
    await click(view.container.querySelector('button[type="submit"]'));
    const chip = () =>
      view!.container.querySelector<HTMLButtonElement>('button[aria-label^="Search in"]')!;
    expect(chip().textContent).toBe("Contracts");
    await click(chip());
    await click(
      [...document.querySelectorAll<HTMLElement>('[role="menuitemcheckbox"]')].find((el) =>
        el.textContent?.includes("Eval corpus"),
      ),
    );
    expect(chip().textContent).toBe("Contracts + 1");
    release(json({ chats: [chat([1])] })); // the old list arrives late
    await settle();
    expect(chip().textContent).toBe("Contracts + 1");
  });
});
