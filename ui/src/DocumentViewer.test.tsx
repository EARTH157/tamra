import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import DocumentViewer from "./DocumentViewer";
import { pageScale } from "./PdfPage";
import {
  buttonByText,
  click,
  json,
  type Mounted,
  mockFetch,
  mount,
  press,
  settle,
} from "./test-utils";
import type { Locate, Source, ViewerRequest } from "./types";

let view: Mounted | undefined;
let created = 0;
let revoked: string[] = [];
let scrolled: Element[] = [];

type Scrollable = { scrollIntoView?: unknown };

beforeEach(() => {
  created = 0;
  revoked = [];
  scrolled = [];
  URL.createObjectURL = vi.fn(() => `blob:page-${++created}`);
  URL.revokeObjectURL = vi.fn((url: string) => {
    revoked.push(url);
  });
  (Element.prototype as Scrollable).scrollIntoView = vi.fn(function (this: Element) {
    scrolled.push(this);
  });
});

afterEach(async () => {
  await view?.unmount();
  view = undefined;
  vi.unstubAllGlobals();
  Reflect.deleteProperty(URL, "createObjectURL");
  Reflect.deleteProperty(URL, "revokeObjectURL");
  Reflect.deleteProperty(Element.prototype, "scrollIntoView");
  window.devicePixelRatio = 1;
});

const snapshot = "Staff get leave. The lease term is three years. Rent is due monthly.";
const sources: Source[] = [
  { n: 1, file_id: 7, file: "docs/lease.pdf", label: "p. 2", text: snapshot, location: {} },
  { n: 2, file_id: 8, file: "notes.txt", label: "line 3", text: "Notice is 30 days.", location: {} },
  { n: 3, file_id: null, file: "gone.txt", label: "", text: "Left the index.", location: {} },
];
const start = snapshot.indexOf("The lease");
const end = snapshot.indexOf(" Rent");
const request: ViewerRequest = {
  messageId: 4,
  n: 1,
  fileId: 7,
  file: "docs/lease.pdf",
  start,
  end,
  selection: "The lease runs three years",
};

const locateUrl = `GET /api/sources/4/1/locate?start=${start}&end=${end}`;
const locatePdf: Locate = {
  file_id: 7,
  kind: "pdf",
  changed: false,
  found: true,
  page_count: 12,
  page: 2,
  start: 0,
  end: 41,
  rects: [
    [0.08, 0.05, 0.62, 0.07],
    [0.08, 0.07, 0.4, 0.09],
  ],
};
const png = () => new Response(new Blob(["png"], { type: "image/png" }));
const pageRoute = (page: number, scale = 1) => `GET /api/files/7/pages/${page}?scale=${scale}`;

const onClose = vi.fn();
const viewer = (over: Partial<ViewerRequest> = {}) => (
  <DocumentViewer request={{ ...request, ...over }} sources={sources} onClose={onClose} />
);

function image(): HTMLImageElement | null {
  return view?.container.ownerDocument.querySelector(".pdf-page img") ?? null;
}
function boxes(): HTMLElement[] {
  return [...document.querySelectorAll<HTMLElement>(".pdf-hit")];
}
const text = (selector: string) => document.querySelector(selector)?.textContent;
const byLabel = (label: string) => document.querySelector<HTMLButtonElement>(`button[aria-label="${label}"]`);

describe("DocumentViewer, PDF", () => {
  it("shows the located page as an image from a blob URL, with the passage's boxes over it", async () => {
    const calls = mockFetch({
      [locateUrl]: () => json(locatePdf),
      [pageRoute(2)]: png,
    });
    view = await mount(viewer());
    expect(calls.map((c) => c.key)).toEqual([locateUrl, pageRoute(2)]);
    expect(image()?.getAttribute("src")).toBe("blob:page-1");
    expect(image()?.getAttribute("alt")).toBe("Page 2 of lease.pdf");
    expect(text(".viewer-readout")).toBe("2 / 12");
    const [first, second] = boxes();
    expect(boxes()).toHaveLength(2);
    expect(first.style.left).toBe("8%");
    expect(first.style.top).toBe("5%");
    expect(first.style.width).toBe("54%");
    expect(first.style.height).toBe("2%");
    expect(second.style.width).toBe("32%");
    // The boxes carry no meaning for assistive technology: the left pane's text does.
    expect(boxes().every((box) => box.getAttribute("aria-hidden") === "true")).toBe(true);
    // The first box is scrolled into view.
    expect(scrolled).toContain(first);
  });

  it("asks for the page image with the token header", async () => {
    const fetchSpy = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) =>
      String(input).includes("/pages/") ? png() : json(locatePdf),
    );
    vi.stubGlobal("fetch", fetchSpy);
    view = await mount(viewer());
    const pageCall = fetchSpy.mock.calls.find(([input]) => String(input).includes("/pages/"));
    expect(pageCall?.[1]?.headers).toHaveProperty("X-Tamra-Token");
  });

  it("sizes the page from its image: the image's pixels over the render scale", async () => {
    window.devicePixelRatio = 2;
    mockFetch({ [locateUrl]: () => json(locatePdf), [pageRoute(2, 2)]: png });
    view = await mount(viewer());
    const img = image();
    Object.defineProperty(img, "naturalWidth", { value: 1190 });
    await act(async () => {
      img?.dispatchEvent(new Event("load"));
    });
    expect((document.querySelector(".pdf-page") as HTMLElement).style.width).toBe("595px");
  });

  it("loads the next and the previous page, with the passage marked only on its own page", async () => {
    const calls = mockFetch({
      [locateUrl]: () => json(locatePdf),
      [pageRoute(2)]: png,
      [pageRoute(3)]: png,
    });
    view = await mount(viewer());
    await click(byLabel("Next page"));
    expect(calls.at(-1)?.key).toBe(pageRoute(3));
    expect(text(".viewer-readout")).toBe("3 / 12");
    expect(image()?.getAttribute("src")).toBe("blob:page-2");
    expect(image()?.getAttribute("alt")).toBe("Page 3 of lease.pdf");
    expect(boxes()).toHaveLength(0);
    await click(byLabel("Previous page"));
    expect(calls.at(-1)?.key).toBe(pageRoute(2));
    expect(boxes()).toHaveLength(2);
  });

  it("stops the page buttons at the first and the last page", async () => {
    mockFetch({
      [locateUrl]: () => json({ ...locatePdf, page: 1, page_count: 2 }),
      [pageRoute(1)]: png,
      [pageRoute(2)]: png,
    });
    view = await mount(viewer());
    expect(byLabel("Previous page")?.disabled).toBe(true);
    await click(byLabel("Next page"));
    expect(byLabel("Next page")?.disabled).toBe(true);
    expect(byLabel("Previous page")?.disabled).toBe(false);
  });

  it("zooms in steps that change the render scale, and keeps the boxes as fractions", async () => {
    const calls = mockFetch({
      [locateUrl]: () => json(locatePdf),
      [pageRoute(2)]: png,
      [pageRoute(2, 1.25)]: png,
      [pageRoute(2, 1.5)]: png,
      [pageRoute(2, 0.75)]: png,
      [pageRoute(2, 0.5)]: png,
    });
    view = await mount(viewer());
    expect(document.querySelectorAll(".viewer-readout")[1]?.textContent).toBe("100%");
    await click(byLabel("Zoom in"));
    expect(calls.at(-1)?.key).toBe(pageRoute(2, 1.25));
    expect(boxes()[0].style.width).toBe("54%");
    await click(byLabel("Zoom in"));
    expect(calls.at(-1)?.key).toBe(pageRoute(2, 1.5));
    await click(byLabel("Zoom out"));
    await click(byLabel("Zoom out"));
    await click(byLabel("Zoom out"));
    expect(calls.at(-1)?.key).toBe(pageRoute(2, 0.75));
    await click(byLabel("Zoom out"));
    expect(calls.at(-1)?.key).toBe(pageRoute(2, 0.5));
    expect(byLabel("Zoom out")?.disabled).toBe(true);
  });

  it("renders at the screen's pixel ratio, capped at 3", () => {
    expect(pageScale(100, 1)).toBe(1);
    expect(pageScale(100, 2)).toBe(2);
    expect(pageScale(150, 2)).toBe(3);
    expect(pageScale(200, 2)).toBe(3);
    expect(pageScale(50, 1)).toBe(0.5);
    expect(pageScale(75, 1.25)).toBe(0.94);
    expect(pageScale(100, 0)).toBe(1);
  });

  it("revokes an object URL when its page is replaced, and the last one on close", async () => {
    mockFetch({
      [locateUrl]: () => json(locatePdf),
      [pageRoute(2)]: png,
      [pageRoute(3)]: png,
    });
    view = await mount(viewer());
    expect(revoked).toEqual([]);
    await click(byLabel("Next page"));
    expect(revoked).toEqual(["blob:page-1"]);
    await view.unmount();
    view = undefined;
    expect(revoked).toEqual(["blob:page-1", "blob:page-2"]);
  });

  it("asks for at most 2 page images at once and never sends one that was left", async () => {
    const pending: Array<(response: Response) => void> = [];
    const requested: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (!url.includes("/pages/")) return Promise.resolve(json({ ...locatePdf, page: 1 }));
        requested.push(url);
        return new Promise<Response>((resolve) => pending.push(resolve));
      }),
    );
    view = await mount(viewer());
    expect(requested).toEqual(["/api/files/7/pages/1?scale=1"]);
    await click(byLabel("Next page"));
    expect(requested).toHaveLength(2);
    // A third page waits for a slot; a fourth replaces it before it was ever sent.
    await click(byLabel("Next page"));
    await click(byLabel("Next page"));
    expect(requested).toHaveLength(2);
    await act(async () => {
      pending[0](png());
    });
    await settle();
    expect(requested).toEqual([
      "/api/files/7/pages/1?scale=1",
      "/api/files/7/pages/2?scale=1",
      "/api/files/7/pages/4?scale=1",
    ]);
    // Let every request finish, so no slot stays taken for the next test.
    await act(async () => {
      for (const resolve of pending) resolve(png());
    });
    await settle();
  });

  it("shows page 1 with no marks when the passage is not in the file, and says so", async () => {
    mockFetch({
      [locateUrl]: () => json({ file_id: 7, kind: "pdf", changed: false, found: false, page_count: 12 }),
      [pageRoute(1)]: png,
    });
    view = await mount(viewer());
    expect(text(".viewer-readout")).toBe("1 / 12");
    expect(image()).not.toBeNull();
    expect(boxes()).toHaveLength(0);
    expect(document.body.textContent).toContain("Tamra could not find this passage in the current file.");
    expect(document.querySelector(".viewer-found")).toBeNull();
  });

  it("warns that the document changed since the answer", async () => {
    mockFetch({
      [locateUrl]: () => json({ ...locatePdf, changed: true }),
      [pageRoute(2)]: png,
    });
    view = await mount(viewer());
    expect(document.body.textContent).toContain("This document changed after the answer.");
  });

  it("shows a page that cannot be rendered as an error", async () => {
    mockFetch({
      [locateUrl]: () => json(locatePdf),
      [pageRoute(2)]: () => json({ detail: "This PDF could not be read." }, 422),
    });
    view = await mount(viewer());
    expect(document.querySelector('[role="alert"]')?.textContent).toBe(
      "Could not show this file. This PDF could not be read.",
    );
  });
});

describe("DocumentViewer, left pane", () => {
  it("shows the answer text, the passage in this file with where it is, and the other sources", async () => {
    mockFetch({ [locateUrl]: () => json(locatePdf), [pageRoute(2)]: png });
    view = await mount(viewer());
    expect(text(".viewer-selected")).toBe("The lease runs three years");
    expect(text(".viewer-found p")).toBe("The lease term is three years.");
    expect(text(".viewer-where")).toBe("page 2");
    // Only the other sources that are still in the index get a card.
    const cards = [...document.querySelectorAll(".viewer-others .source-card")];
    expect(cards.map((card) => card.textContent)).toEqual(["2notes.txtline 3"]);
    expect(text(".viewer-note")).toBe(
      "Yellow marks the same passage on both sides, so you can read them against each other.",
    );
    expect(text("#viewer-title")).toBe("lease.pdf");
    expect(text(".viewer-title span")).toBe("docs");
  });

  it("slices the passage by code points", async () => {
    const emoji: Source = {
      n: 1,
      file_id: 7,
      file: "a.pdf",
      label: "",
      text: "😀😀 The lease term. Rent.",
      location: {},
    };
    mockFetch({ "GET /api/sources/4/1/locate?start=3&end=18": () => json(locatePdf), [pageRoute(2)]: png });
    view = await mount(
      <DocumentViewer request={{ ...request, start: 3, end: 18 }} sources={[emoji]} onClose={onClose} />,
    );
    expect(text(".viewer-found p")).toBe("The lease term.");
  });

  it("switches to another source of the answer: it locates the whole snapshot of that source", async () => {
    const calls = mockFetch({
      [locateUrl]: () => json(locatePdf),
      [pageRoute(2)]: png,
      "GET /api/sources/4/2/locate": () =>
        json({ file_id: 8, kind: "text", changed: false, found: true, start: 1, end: 2 }),
      "GET /api/files/8/text": () => json({ kind: "text", lines: ["Notice is 30 days."] }),
    });
    view = await mount(viewer());
    await click(document.querySelector(".viewer-others .source-card"));
    expect(calls.map((c) => c.key).slice(-2)).toEqual([
      "GET /api/sources/4/2/locate",
      "GET /api/files/8/text",
    ]);
    expect(text("#viewer-title")).toBe("notes.txt");
    expect(text(".viewer-found p")).toBe("Notice is 30 days.");
    expect(text(".viewer-where")).toBe("line 1");
    // The checked answer text belongs to the first passage only, and the first source is now a card.
    expect(document.querySelector(".viewer-selected")).toBeNull();
    expect(
      [...document.querySelectorAll(".viewer-others .source-card")].map((card) => card.textContent),
    ).toEqual(["1lease.pdfp. 2"]);
    // A text file has no page or zoom controls.
    expect(byLabel("Next page")).toBeNull();
    expect(byLabel("Zoom in")).toBeNull();
    expect(document.querySelector(".pdf-page")).toBeNull();
    expect(revoked).toEqual(["blob:page-1"]);
  });

  it("opened from a source card it has no answer text to compare", async () => {
    mockFetch({ "GET /api/sources/4/1/locate": () => json(locatePdf), [pageRoute(2)]: png });
    view = await mount(viewer({ start: null, end: null, selection: null }));
    expect(document.querySelector(".viewer-selected")).toBeNull();
    expect(text(".viewer-found p")).toBe(snapshot);
  });
});

describe("DocumentViewer, text files", () => {
  const textRequest = { n: 2, fileId: 8, file: "notes.txt", start: 0, end: 18 };
  const lines = ["Intro line", "", "Rent is due", "on the first day.", "Notice is 30 days.", "End"];

  it("numbers the lines, marks the located lines and scrolls to them", async () => {
    mockFetch({
      "GET /api/sources/4/2/locate?start=0&end=18": () =>
        json({ file_id: 8, kind: "text", changed: false, found: true, start: 3, end: 5 }),
      "GET /api/files/8/text": () => json({ kind: "text", lines }),
    });
    view = await mount(viewer(textRequest));
    const rows = [...document.querySelectorAll<HTMLElement>(".viewer-line")];
    expect(rows.map((row) => row.querySelector(".no")?.textContent)).toEqual([
      "1",
      "2",
      "3",
      "4",
      "5",
      "6",
    ]);
    expect(rows.map((row) => row.classList.contains("hit"))).toEqual([
      false,
      false,
      true,
      true,
      false,
      false,
    ]);
    expect(rows[2].textContent).toContain("Rent is due");
    expect(scrolled).toEqual([rows[2]]);
    expect(text(".viewer-where")).toBe("lines 3–4");
  });

  it("shows a docx by paragraph with its headings bold and the located paragraphs marked", async () => {
    mockFetch({
      "GET /api/sources/4/2/locate?start=0&end=18": () =>
        json({ file_id: 8, kind: "docx", changed: false, found: true, start: 1, end: 2 }),
      "GET /api/files/8/text": () =>
        json({
          kind: "docx",
          paragraphs: [
            { index: 0, text: "Lease", heading: true },
            { index: 1, text: "Rent is due monthly.", heading: false },
            { index: 2, text: "Notice is 30 days.", heading: false },
          ],
        }),
    });
    view = await mount(viewer(textRequest));
    const rows = [...document.querySelectorAll<HTMLElement>(".viewer-line")];
    expect(rows.map((row) => row.classList.contains("heading"))).toEqual([true, false, false]);
    expect(rows.map((row) => row.classList.contains("hit"))).toEqual([false, true, false]);
    expect(rows.map((row) => row.querySelector(".no")?.textContent)).toEqual(["1", "2", "3"]);
    expect(scrolled).toEqual([rows[1]]);
    expect(text(".viewer-where")).toBe("paragraph 2");
  });

  it("shows the text with no mark when the passage is not found, and says so", async () => {
    mockFetch({
      "GET /api/sources/4/2/locate?start=0&end=18": () =>
        json({ file_id: 8, kind: "text", changed: true, found: false }),
      "GET /api/files/8/text": () => json({ kind: "text", lines }),
    });
    view = await mount(viewer(textRequest));
    expect(document.querySelectorAll(".viewer-line")).toHaveLength(6);
    expect(document.querySelector(".viewer-line.hit")).toBeNull();
    expect(scrolled).toEqual([]);
    expect(document.body.textContent).toContain("Tamra could not find this passage in the current file.");
    expect(document.body.textContent).toContain("This document changed after the answer.");
  });

  it("says when a long file was cut", async () => {
    mockFetch({
      "GET /api/sources/4/2/locate?start=0&end=18": () =>
        json({ file_id: 8, kind: "text", changed: false, found: true, start: 1, end: 2 }),
      "GET /api/files/8/text": () => json({ kind: "text", lines, truncated: true }),
    });
    view = await mount(viewer(textRequest));
    expect(document.body.textContent).toContain("This file is long. Only the start is shown.");
  });

  it("shows why a file cannot be read", async () => {
    mockFetch({
      "GET /api/sources/4/2/locate?start=0&end=18": () => json({ detail: "This file could not be read." }, 422),
    });
    view = await mount(viewer(textRequest));
    expect(document.querySelector('[role="alert"]')?.textContent).toBe(
      "Could not show this file. This file could not be read.",
    );
    expect(document.querySelector(".viewer-line")).toBeNull();
  });
});

describe("DocumentViewer, header", () => {
  const routes = () => ({ [locateUrl]: () => json(locatePdf), [pageRoute(2)]: png });

  it("opens the file with the default app", async () => {
    const calls = mockFetch({
      ...routes(),
      "POST /api/files/7/open": () => new Response(null, { status: 204 }),
    });
    view = await mount(viewer());
    await click(buttonByText(document.body, "Open with default app"));
    expect(calls.at(-1)?.key).toBe("POST /api/files/7/open");
    expect(document.querySelector('[role="alert"]')).toBeNull();
  });

  it("replaces the button with a note where opening is not possible (the 501 of the dev server)", async () => {
    mockFetch({
      ...routes(),
      "POST /api/files/7/open": () =>
        json({ detail: "Opening files is only available in the Tamra window." }, 501),
    });
    view = await mount(viewer());
    await click(buttonByText(document.body, "Open with default app"));
    expect(buttonByText(document.body, "Open with default app")).toBeUndefined();
    expect(text(".viewer-unavailable")).toBe("Only available in the Tamra window.");
  });

  it("shows another failure to open as an error and keeps the button", async () => {
    mockFetch({
      ...routes(),
      "POST /api/files/7/open": () => json({ detail: "Could not open the file." }, 500),
    });
    view = await mount(viewer());
    await click(buttonByText(document.body, "Open with default app"));
    expect(document.querySelector('[role="alert"]')?.textContent).toBe(
      "Could not open the file. Could not open the file.",
    );
    expect(buttonByText(document.body, "Open with default app")).toBeDefined();
  });

  it("closes with the button and with Esc, and is a labelled dialog", async () => {
    mockFetch(routes());
    onClose.mockClear();
    view = await mount(viewer());
    const dialog = document.querySelector('[role="dialog"]');
    expect(dialog?.getAttribute("aria-modal")).toBe("true");
    expect(document.getElementById(dialog?.getAttribute("aria-labelledby") ?? "")?.textContent).toBe(
      "lease.pdf",
    );
    await click(byLabel("Close"));
    expect(onClose).toHaveBeenCalledTimes(1);
    await press(document.body, "Escape");
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("puts focus on the close button and gives it back on close", async () => {
    mockFetch(routes());
    const opener = document.createElement("button");
    document.body.appendChild(opener);
    opener.focus();
    view = await mount(viewer());
    expect(document.activeElement).toBe(byLabel("Close"));
    await view.unmount();
    view = undefined;
    expect(document.activeElement).toBe(opener);
    opener.remove();
  });

  it("shows why the file cannot be shown when it is gone", async () => {
    mockFetch({ [locateUrl]: () => json({ detail: "File not found." }, 404) });
    view = await mount(viewer());
    expect(document.querySelector('[role="alert"]')?.textContent).toBe(
      "Could not show this file. File not found.",
    );
    expect(byLabel("Next page")).toBeNull();
  });
});
