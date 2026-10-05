import { act } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import ChatView from "./ChatView";
import {
  buttonByText,
  type Call,
  click,
  json,
  type Mounted,
  mockFetch,
  mount,
  press,
  settle,
  typeInto,
} from "./test-utils";
import type { Source } from "./types";

let view: Mounted | undefined;

afterEach(async () => {
  window.getSelection()?.removeAllRanges();
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
const content = "Staff get 6 days [1]. The notice period is 30 days [2].";
const sources: Source[] = [
  {
    n: 1,
    file_id: 3,
    file: "leave.pdf",
    label: "p. 2",
    text: "Staff get 6 days of leave a year.",
    location: {},
  },
  {
    n: 2,
    file_id: 4,
    file: "notes.txt",
    label: "line 3",
    text: "The notice period is 30 days.",
    location: {},
  },
];
const message = (id: number, role: "user" | "assistant", text: string, found: Source[] = []) => ({
  id,
  role,
  content: text,
  provider: null,
  model: null,
  created_at: "t",
  sources: found,
});
const match = (n: number, text: string) => ({
  n,
  start: 0,
  end: text.length,
  text,
  label: "strong",
  file_id: n + 2,
  file: sources[n - 1].file,
  location_label: sources[n - 1].label,
  changed: false,
});

let calls: Call[] = [];

/** A chat with one saved answer; attribution answers with `attribution`. */
async function open(attribution: () => Response) {
  calls = mockFetch({
    "GET /api/chats/5": () =>
      json({ ...chat, messages: [message(1, "user", "Leave?"), message(2, "assistant", content, sources)] }),
    "POST /api/attribution": attribution,
    "GET /api/sources/2/1/locate?start=0&end=33": () => json({ detail: "File not found." }, 404),
    "GET /api/sources/2/2/locate?start=0&end=29": () => json({ detail: "File not found." }, 404),
  });
  view = await mount(<ChatView {...base} chatId={5} />);
}

const answerText = () => view!.container.querySelector<HTMLElement>(".answer-text")!;
const attributionBody = () => calls.find((c) => c.key === "POST /api/attribution")?.body;

/** Select [from, to) of the first text span, then release the mouse like a user would. */
async function selectIn(span: Element, from: number, to: number) {
  const node = span.firstChild!;
  const range = document.createRange();
  range.setStart(node, from);
  range.setEnd(node, to);
  const selection = window.getSelection()!;
  selection.removeAllRanges();
  selection.addRange(range);
  await act(async () => {
    document.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
  });
}

const checkButton = () => buttonByText(view!.container, "Check source");

describe("checking a source from an answer", () => {
  it("shows Check source on a selection and sends the raw text and the message id", async () => {
    await open(() => json({ matches: [match(1, sources[0].text)] }));
    expect(checkButton()).toBeUndefined();
    // "6 days" is the end of the first span (the space before the chip is trimmed).
    const first = answerText().querySelector("span[data-start]")!;
    await selectIn(first, 10, first.textContent!.length);
    expect(checkButton()).toBeDefined();
    await click(checkButton());
    expect(attributionBody()).toEqual({ message_id: 2, selection: "6 days" });
    const panel = view!.container.querySelector(".source-panel")!;
    expect(panel.textContent).toContain("Strong match");
    expect(panel.querySelector(".snapshot-hit")?.textContent).toBe(sources[0].text);
    expect(checkButton()).toBeUndefined();
  });

  it("sends the marker too when the selection spans a chip", async () => {
    await open(() => json({ matches: [match(1, sources[0].text)] }));
    const spans = answerText().querySelectorAll("span[data-start]");
    const range = document.createRange();
    range.setStart(spans[0].firstChild!, 10);
    range.setEnd(spans[1].firstChild!, 1); // after the "." that follows the chip
    window.getSelection()!.removeAllRanges();
    window.getSelection()!.addRange(range);
    await act(async () => {
      document.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
    });
    await click(checkButton());
    expect(attributionBody()).toEqual({ message_id: 2, selection: "6 days [1]." });
  });

  it("marks the checked span in the answer until the panel closes", async () => {
    await open(() => json({ matches: [match(1, sources[0].text)] }));
    const first = answerText().querySelector("span[data-start]")!;
    await selectIn(first, 0, 5); // "Staff"
    await click(checkButton());
    expect(answerText().querySelector("mark.answer-hit")?.textContent).toBe("Staff");
    expect(answerText().textContent).toBe("Staff get 6 days 1. The notice period is 30 days 2.");
    await click(view!.container.querySelector('button[aria-label="Close source"]'));
    expect(view!.container.querySelector(".source-panel")).toBeNull();
    expect(answerText().querySelector("mark")).toBeNull();
  });

  it("sends n and the sentence when a chip is clicked, and fills that chip", async () => {
    await open(() => json({ matches: [match(2, sources[1].text)] }));
    await click(answerText().querySelectorAll("button.cite")[1]);
    expect(attributionBody()).toEqual({
      message_id: 2,
      selection: "The notice period is 30 days",
      n: 2,
    });
    expect(answerText().querySelector("mark.answer-hit")?.textContent).toBe(
      "The notice period is 30 days",
    );
    const chips = [...answerText().querySelectorAll("button.cite")];
    expect(chips.map((chip) => chip.classList.contains("active"))).toEqual([false, true]);
    expect(view!.container.querySelector("header strong")?.textContent).toBe("notes.txt");
  });

  it("shows no button for a collapsed selection, and hides it on Escape", async () => {
    await open(() => json({ matches: [] }));
    const first = answerText().querySelector("span[data-start]")!;
    await selectIn(first, 3, 3);
    expect(checkButton()).toBeUndefined();
    await selectIn(first, 0, 5);
    expect(checkButton()).toBeDefined();
    await press(document.body, "Escape");
    expect(checkButton()).toBeUndefined();
  });

  it("keeps the button away after Escape: the keyup of the same key does not bring it back", async () => {
    await open(() => json({ matches: [] }));
    const first = answerText().querySelector("span[data-start]")!;
    await selectIn(first, 0, 5);
    expect(checkButton()).toBeDefined();
    await act(async () => {
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    });
    await act(async () => {
      document.dispatchEvent(new KeyboardEvent("keyup", { key: "Escape", bubbles: true }));
    });
    expect(checkButton()).toBeUndefined();
    // A new selection shows it again.
    await selectIn(first, 0, 9);
    expect(checkButton()).toBeDefined();
  });

  it("shows the button again for the same selection after the mouse is pressed and released", async () => {
    await open(() => json({ matches: [] }));
    const first = answerText().querySelector("span[data-start]")!;
    await selectIn(first, 0, 5);
    await act(async () => {
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    });
    expect(checkButton()).toBeUndefined();
    await act(async () => {
      document.body.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    });
    await selectIn(first, 0, 5);
    expect(checkButton()).toBeDefined();
  });

  it("places the button under the selection, inside the window, and above it near the bottom", async () => {
    await open(() => json({ matches: [] }));
    const first = answerText().querySelector("span[data-start]")!;
    let rect = { right: 1000, top: 100, bottom: 120 };
    const rects = vi.fn(() => [rect]);
    Object.defineProperty(Range.prototype, "getClientRects", { configurable: true, value: rects });
    const width = vi.spyOn(HTMLElement.prototype, "offsetWidth", "get").mockReturnValue(150);
    const height = vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockReturnValue(32);
    try {
      await selectIn(first, 0, 5);
      expect(checkButton()?.style.left).toBe(`${window.innerWidth - 150 - 8}px`);
      expect(checkButton()?.style.top).toBe("126px");
      rect = { right: 40, top: window.innerHeight - 30, bottom: window.innerHeight - 10 };
      await selectIn(first, 0, 9);
      expect(checkButton()?.style.left).toBe("40px");
      expect(checkButton()?.style.top).toBe(`${window.innerHeight - 30 - 6 - 32}px`);
    } finally {
      width.mockRestore();
      height.mockRestore();
      delete (Range.prototype as { getClientRects?: unknown }).getClientRects;
    }
  });

  it("hides the button on scroll and when the selection is cleared", async () => {
    await open(() => json({ matches: [] }));
    const first = answerText().querySelector("span[data-start]")!;
    await selectIn(first, 0, 5);
    await act(async () => {
      view!.container.dispatchEvent(new Event("scroll"));
      window.dispatchEvent(new Event("scroll"));
    });
    expect(checkButton()).toBeUndefined();
    await selectIn(first, 0, 5);
    expect(checkButton()).toBeDefined();
    await act(async () => {
      window.getSelection()!.removeAllRanges();
      document.dispatchEvent(new Event("selectionchange"));
    });
    expect(checkButton()).toBeUndefined();
  });

  it("hides the button on a click elsewhere", async () => {
    await open(() => json({ matches: [] }));
    const first = answerText().querySelector("span[data-start]")!;
    await selectIn(first, 0, 5);
    await act(async () => {
      document.body.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    });
    expect(checkButton()).toBeUndefined();
  });

  it("does not offer it for a selection that spans two messages", async () => {
    calls = mockFetch({
      "GET /api/chats/5": () =>
        json({
          ...chat,
          messages: [
            message(2, "assistant", "One answer here.", []),
            message(3, "assistant", "Another answer here.", []),
          ],
        }),
    });
    view = await mount(<ChatView {...base} chatId={5} />);
    const [a, b] = [...view.container.querySelectorAll(".answer-text span[data-start]")];
    const range = document.createRange();
    range.setStart(a.firstChild!, 4);
    range.setEnd(b.firstChild!, 7);
    window.getSelection()!.removeAllRanges();
    window.getSelection()!.addRange(range);
    await act(async () => {
      document.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
    });
    expect(checkButton()).toBeUndefined();
  });

  it("does not offer it in the answer that is still being streamed", async () => {
    const encoder = new TextEncoder();
    let push: (event: unknown) => void = () => {};
    let finish: () => void = () => {};
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        push = (event) => controller.enqueue(encoder.encode(`data: ${JSON.stringify(event)}\n\n`));
        finish = () => controller.close();
      },
    });
    calls = mockFetch({
      "GET /api/chats/5": () => json({ ...chat, messages: [] }),
      "POST /api/chats/5/messages": () =>
        new Response(stream, { headers: { "Content-Type": "text/event-stream" } }),
    });
    view = await mount(<ChatView {...base} chatId={5} />);
    await typeInto(view.container.querySelector("textarea"), "q");
    await click(view.container.querySelector('button[type="submit"]'));
    push({ type: "sources", sources });
    push({ type: "token", text: "Staff get 6 days [1]." });
    await settle();
    const first = answerText().querySelector("span[data-start]")!;
    await selectIn(first, 0, 5);
    expect(checkButton()).toBeUndefined();
    finish();
    await settle();
  });

  it("opens the source as it is when too little text comes before the chip", async () => {
    calls = mockFetch({
      "GET /api/chats/5": () =>
        json({ ...chat, messages: [message(2, "assistant", "A [1] and more [2].", sources)] }),
    });
    view = await mount(<ChatView {...base} chatId={5} />);
    await click(answerText().querySelectorAll("button.cite")[0]); // only "A" before it
    expect(attributionBody()).toBeUndefined();
    expect(view.container.querySelector(".source-panel .paper")?.textContent).toBe(sources[0].text);
    expect(view.container.querySelector(".source-panel .compare")).toBeNull();
  });

  it("shows an error when the check fails", async () => {
    await open(() => json({ detail: "The embedding model is unavailable." }, 503));
    await click(answerText().querySelectorAll("button.cite")[0]);
    expect(view!.container.querySelector('.source-panel [role="alert"]')?.textContent).toBe(
      "Could not check this selection. The embedding model is unavailable.",
    );
  });

  it("shows no clear source and lists the sources when nothing matches", async () => {
    await open(() => json({ matches: [] }));
    const first = answerText().querySelector("span[data-start]")!;
    await selectIn(first, 0, 5);
    await click(checkButton());
    const panel = view!.container.querySelector(".source-panel")!;
    expect(panel.textContent).toContain("No clear source found for this selection.");
    await click(panel.querySelectorAll(".source-card")[1]);
    // A source opened from the list is shown as it is, without the compare section.
    expect(view!.container.querySelector(".source-panel .compare")).toBeNull();
    expect(view!.container.querySelector(".source-panel .paper")?.textContent).toBe(
      sources[1].text,
    );
    expect(answerText().querySelector("mark")).toBeNull();
  });

  it("ignores the answer of a check that was replaced", async () => {
    let releaseFirst: () => void = () => {};
    let requests = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/chats/5") {
          return json({ ...chat, messages: [message(2, "assistant", content, sources)] });
        }
        if (url === "/api/attribution") {
          requests++;
          if (requests === 1) {
            await new Promise<void>((resolve) => {
              releaseFirst = resolve;
            });
            return json({ matches: [match(1, sources[0].text)] });
          }
          return json({ matches: [match(2, sources[1].text)] });
        }
        return json({ detail: "File not found." }, 404); // locate
      }),
    );
    view = await mount(<ChatView {...base} chatId={5} />);
    await click(answerText().querySelectorAll("button.cite")[0]); // pending
    await click(answerText().querySelectorAll("button.cite")[1]); // replaces it
    expect(view.container.querySelector("header strong")?.textContent).toBe("notes.txt");
    await act(async () => releaseFirst());
    await settle();
    expect(view.container.querySelector("header strong")?.textContent).toBe("notes.txt");
    expect(answerText().querySelector("mark.answer-hit")?.textContent).toBe(
      "The notice period is 30 days",
    );
  });
});

describe("opening the document viewer from the source panel", () => {
  it("opens the file at the checked passage over the chat, and Esc closes it", async () => {
    calls = mockFetch({
      "GET /api/chats/5": () =>
        json({ ...chat, messages: [message(1, "user", "Leave?"), message(2, "assistant", content, sources)] }),
      "POST /api/attribution": () => json({ matches: [match(2, sources[1].text)] }),
      "GET /api/sources/2/2/locate?start=0&end=29": () =>
        json({ file_id: 4, kind: "text", changed: false, found: true, start: 3, end: 4 }),
      "GET /api/files/4/text": () => json({ kind: "text", lines: ["Terms", "Notice", "30 days"] }),
    });
    view = await mount(<ChatView {...base} chatId={5} />);
    await click(answerText().querySelectorAll("button.cite")[1]);
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    await click(buttonByText(view.container, "Open file"));
    const dialog = document.querySelector('[role="dialog"]')!;
    expect(dialog.querySelector("h2")?.textContent).toBe("notes.txt");
    // The checked sentence and the passage of the file are side by side.
    expect(dialog.querySelector(".viewer-selected")?.textContent).toBe("The notice period is 30 days");
    expect(dialog.querySelector(".viewer-found p")?.textContent).toBe(sources[1].text);
    expect(dialog.querySelector(".viewer-line.hit")?.textContent).toContain("30 days");
    // The answer's other source is offered.
    expect(dialog.querySelector(".viewer-others")?.textContent).toContain("leave.pdf");
    await press(document.body, "Escape");
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(view.container.querySelector(".source-panel")).not.toBeNull();
  });
});
