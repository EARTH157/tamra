import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import SourcePanel, { type Check } from "./SourcePanel";
import { buttonByText, click, json, type Mounted, mockFetch, mount, settle } from "./test-utils";
import type { Match, Source } from "./types";

let view: Mounted | undefined;

afterEach(async () => {
  await view?.unmount();
  view = undefined;
  vi.unstubAllGlobals();
});

const snapshot = "Staff get leave. The lease term is three years. Rent is due monthly.";
const sources: Source[] = [
  { n: 1, file_id: 7, file: "docs/lease.pdf", label: "p. 2", text: snapshot, location: {} },
  { n: 2, file_id: 8, file: "notes.txt", label: "line 3", text: "Notice is 30 days.", location: {} },
];
const start = snapshot.indexOf("The lease");
const end = snapshot.indexOf(" Rent");
const match: Match = {
  n: 1,
  start,
  end,
  text: snapshot.slice(start, end),
  label: "strong",
  file_id: 7,
  file: "docs/lease.pdf",
  location_label: "p. 2",
  changed: false,
};
const second: Match = {
  ...match,
  n: 2,
  start: 0,
  end: 18,
  text: "Notice is 30 days.",
  label: "partial",
  file_id: 8,
  file: "notes.txt",
  location_label: "line 3",
};
const check = (over: Partial<Check> = {}): Check => ({
  id: 1,
  messageId: 4,
  selection: "The lease runs three years",
  start: 0,
  end: 26,
  n: null,
  status: "ready",
  matches: [match],
  index: 0,
  error: null,
  ...over,
});

const noop = () => {};
const base = {
  sources,
  messageId: 4,
  check: null,
  source: null,
  onIndexChange: noop,
  onOpenSource: noop,
  onOpenViewer: noop,
  onClose: noop,
};

const locatePdf = {
  file_id: 7,
  kind: "pdf",
  changed: false,
  found: true,
  page_count: 32,
  page: 14,
  start: 0,
  end: 10,
  rects: [],
};
const locateUrl = (n: number, m: Match) =>
  `GET /api/sources/4/${n}/locate?start=${m.start}&end=${m.end}`;
const subtitle = () => view?.container.querySelector("header .source-title span")?.textContent;

describe("SourcePanel", () => {
  it("compares the answer with the document and highlights the passage in the snapshot", async () => {
    mockFetch({ [locateUrl(1, match)]: () => json(locatePdf) });
    view = await mount(<SourcePanel {...base} check={check()} />);
    const panel = view.container.querySelector(".source-panel")!;
    const rows = [...panel.querySelectorAll(".compare-row")].map((row) => row.textContent);
    expect(rows).toEqual([
      "AnswerThe lease runs three years",
      "DocumentThe lease term is three years.",
    ]);
    expect(panel.querySelector(".match-badge")?.textContent).toBe("Strong match");
    expect(panel.querySelector(".snapshot-hit")?.textContent).toBe("The lease term is three years.");
    expect(panel.querySelector(".paper")?.textContent).toBe(snapshot);
    expect(panel.querySelector("header strong")?.textContent).toBe("lease.pdf");
    expect(panel.querySelector(".match-nav")).toBeNull(); // one match: nothing to step through
    expect(panel.querySelector(".source-warning")).toBeNull();
  });

  it("shows the located page of a PDF", async () => {
    mockFetch({ [locateUrl(1, match)]: () => json(locatePdf) });
    view = await mount(<SourcePanel {...base} check={check()} />);
    expect(subtitle()).toBe("page 14 of 32");
  });

  it("keeps the saved label when the passage cannot be located", async () => {
    mockFetch({ [locateUrl(1, match)]: () => json({ detail: "File not found." }, 404) });
    view = await mount(<SourcePanel {...base} check={check()} />);
    expect(subtitle()).toBe("p. 2");
  });

  it("shows the lines of a located text file", async () => {
    mockFetch({
      [locateUrl(2, second)]: () =>
        json({ file_id: 8, kind: "text", changed: false, found: true, start: 3, end: 5 }),
    });
    view = await mount(<SourcePanel {...base} check={check({ matches: [second] })} />);
    expect(subtitle()).toBe("lines 3–4");
  });

  it("labels a partial match and steps through several matches", async () => {
    mockFetch({
      [locateUrl(1, match)]: () => json(locatePdf),
      [locateUrl(2, second)]: () =>
        json({ file_id: 8, kind: "text", changed: false, found: false }),
    });
    const onIndexChange = vi.fn();
    view = await mount(
      <SourcePanel
        {...base}
        check={check({ matches: [match, second] })}
        onIndexChange={onIndexChange}
      />,
    );
    const nav = view.container.querySelector(".match-nav")!;
    expect(nav.textContent).toBe("1 of 2");
    const previous = nav.querySelector<HTMLButtonElement>('button[aria-label="Previous match"]')!;
    const next = nav.querySelector<HTMLButtonElement>('button[aria-label="Next match"]')!;
    expect(previous.disabled).toBe(true);
    await click(next);
    expect(onIndexChange).toHaveBeenCalledWith(1);

    await view.unmount();
    view = await mount(
      <SourcePanel
        {...base}
        check={check({ matches: [match, second], index: 1 })}
        onIndexChange={onIndexChange}
      />,
    );
    expect(view.container.querySelector(".match-badge")?.textContent).toBe("Partial match");
    expect(view.container.querySelector(".match-nav")?.textContent).toBe("2 of 2");
    expect(view.container.querySelector("header strong")?.textContent).toBe("notes.txt");
    expect(view.container.querySelector(".snapshot-hit")?.textContent).toBe("Notice is 30 days.");
    expect(
      view.container.querySelector<HTMLButtonElement>('button[aria-label="Next match"]')?.disabled,
    ).toBe(true);
  });

  it("warns when the document changed after the answer", async () => {
    mockFetch({
      [locateUrl(1, match)]: () => json({ ...locatePdf, changed: true, found: false }),
    });
    view = await mount(
      <SourcePanel {...base} check={check({ matches: [{ ...match, changed: true }] })} />,
    );
    expect(view.container.querySelector(".source-warning")?.textContent).toBe(
      "This document changed after the answer. Showing the text Tamra used then.",
    );
    expect(view.container.querySelector(".snapshot-hit")).not.toBeNull();
  });

  it("says no clear source was found and lists the sources of the answer to open", async () => {
    const onOpenSource = vi.fn();
    view = await mount(
      <SourcePanel {...base} check={check({ matches: [] })} onOpenSource={onOpenSource} />,
    );
    expect(view.container.querySelector(".source-body")?.textContent).toContain(
      "No clear source found for this selection.",
    );
    expect(view.container.querySelector(".match-badge")).toBeNull();
    expect(view.container.querySelectorAll(".source-card").length).toBe(2);
    await click(view.container.querySelectorAll(".source-card")[1]);
    expect(onOpenSource).toHaveBeenCalledWith(sources[1]);
    expect(buttonByText(view.container, "Open file")).toBeUndefined();
  });

  it("shows progress while checking", async () => {
    view = await mount(<SourcePanel {...base} check={check({ status: "loading", matches: [] })} />);
    expect(view.container.querySelector('[role="status"]')?.textContent).toBe(
      "Checking the sources…",
    );
    expect(view.container.querySelector(".compare-row")?.textContent).toContain("The lease runs");
  });

  it("shows an alert when the check failed", async () => {
    view = await mount(
      <SourcePanel
        {...base}
        check={check({ status: "error", matches: [], error: "The embedding model is unavailable." })}
      />,
    );
    expect(view.container.querySelector('[role="alert"]')?.textContent).toBe(
      "Could not check this selection. The embedding model is unavailable.",
    );
  });

  it("opens the viewer at the shown passage", async () => {
    mockFetch({ [locateUrl(1, match)]: () => json(locatePdf) });
    const onOpenViewer = vi.fn();
    view = await mount(<SourcePanel {...base} check={check()} onOpenViewer={onOpenViewer} />);
    await click(buttonByText(view.container, "Open file"));
    expect(onOpenViewer).toHaveBeenCalledWith({
      messageId: 4,
      n: 1,
      fileId: 7,
      file: "docs/lease.pdf",
      start,
      end,
    });
  });

  it("shows a source as it is, and opens the whole snapshot in the viewer", async () => {
    const onOpenViewer = vi.fn();
    view = await mount(<SourcePanel {...base} source={sources[1]} onOpenViewer={onOpenViewer} />);
    expect(view.container.querySelector(".compare")).toBeNull();
    expect(view.container.querySelector(".paper")?.textContent).toBe("Notice is 30 days.");
    expect(view.container.querySelector("header strong")?.textContent).toBe("notes.txt");
    await click(buttonByText(view.container, "Open file"));
    expect(onOpenViewer).toHaveBeenCalledWith({
      messageId: 4,
      n: 2,
      fileId: 8,
      file: "notes.txt",
      start: null,
      end: null,
    });
  });

  it("offers no file to open while the answer streams or for a file that left the index", async () => {
    view = await mount(<SourcePanel {...base} messageId={null} source={sources[0]} />);
    expect(buttonByText(view.container, "Open file")).toBeUndefined();
    await view.unmount();
    view = await mount(<SourcePanel {...base} source={{ ...sources[0], file_id: null }} />);
    expect(buttonByText(view.container, "Open file")).toBeUndefined();
  });

  it("reads the core's offsets as code points when the snapshot has an emoji before the window", async () => {
    const text = "😀😀 Leave is six days. Rent is monthly.";
    const startCp = Array.from("😀😀 ").length; // 3 code points, 5 UTF-16 units
    const endCp = startCp + Array.from("Leave is six days.").length;
    const emoji: Match = { ...match, start: startCp, end: endCp, text: "Leave is six days." };
    mockFetch({ [locateUrl(1, emoji)]: () => json(locatePdf) });
    view = await mount(
      <SourcePanel
        {...base}
        sources={[{ ...sources[0], text }, sources[1]]}
        check={check({ matches: [emoji] })}
      />,
    );
    expect(view.container.querySelector(".snapshot-hit")?.textContent).toBe("Leave is six days.");
    expect(view.container.querySelector(".paper")?.textContent).toBe(text);
  });

  it("shows no snapshot when the answer's sources are missing", async () => {
    mockFetch({ [locateUrl(1, match)]: () => json(locatePdf) });
    view = await mount(<SourcePanel {...base} sources={[]} check={check()} />);
    expect(view.container.querySelector(".paper")).toBeNull();
    expect(view.container.querySelector(".snapshot-hit")).toBeNull();
    expect(view.container.querySelector(".compare-row")).not.toBeNull();
  });

  it("does not show the page of the previous match while the next one is being located", async () => {
    const calls = new Map<string, () => void>();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/locate?start=0&end=18")) {
          await new Promise<void>((resolve) => calls.set(url, resolve)); // never answers in time
          return json({ file_id: 8, kind: "pdf", changed: false, found: true, page: 9, page_count: 9 });
        }
        return json(locatePdf);
      }),
    );
    function Host() {
      const [index, setIndex] = useState(0);
      return (
        <SourcePanel
          {...base}
          check={check({ matches: [match, second], index })}
          onIndexChange={setIndex}
        />
      );
    }
    view = await mount(<Host />);
    expect(subtitle()).toBe("page 14 of 32");
    await click(view.container.querySelector('button[aria-label="Next match"]'));
    expect(subtitle()).toBe("line 3"); // the saved label, not the old page
    for (const release of calls.values()) release();
    await settle();
    expect(subtitle()).toBe("page 9 of 9");
  });

  it("closes", async () => {
    const onClose = vi.fn();
    view = await mount(<SourcePanel {...base} source={sources[0]} onClose={onClose} />);
    await click(view.container.querySelector('button[aria-label="Close source"]'));
    expect(onClose).toHaveBeenCalled();
  });
});
