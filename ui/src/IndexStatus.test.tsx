import { afterEach, describe, expect, it, vi } from "vitest";
import IndexStatus from "./IndexStatus";
import { buttonByText, click, type Mounted, mount } from "./test-utils";

let view: Mounted | undefined;

afterEach(async () => {
  await view?.unmount();
  view = undefined;
});

const collection = { id: 1, name: "Contracts", folder_path: "C:/contracts", created_at: "t" };
const counts = { pending: 1, indexing: 1, indexed: 3, failed: 1, skipped: 0 };

describe("IndexStatus", () => {
  it("shows progress while indexing", async () => {
    const index = { stale: false, counts, current: "b.pdf", error: null, problems: [] };
    view = await mount(
      <IndexStatus
        collection={collection}
        index={index}
        disabled={false}
        onChangeFolder={vi.fn()}
        onRebuild={vi.fn()}
      />,
    );
    const text = view.container.textContent ?? "";
    expect(text).toContain("Contracts");
    expect(text).toContain("C:/contracts");
    expect(text).toContain("3 of 6 files indexed");
    expect(text).toContain("Indexing b.pdf");
    const bar = view.container.querySelector('[role="progressbar"]');
    expect(bar?.getAttribute("aria-valuenow")).toBe("3");
    expect(bar?.getAttribute("aria-valuemax")).toBe("6");
  });

  it("shows a check when every file is indexed", async () => {
    const done = { pending: 0, indexing: 0, indexed: 128, failed: 0, skipped: 0 };
    const index = { stale: false, counts: done, current: null, error: null, problems: [] };
    view = await mount(
      <IndexStatus
        collection={collection}
        index={index}
        disabled={false}
        onChangeFolder={vi.fn()}
        onRebuild={vi.fn()}
      />,
    );
    expect(view.container.textContent).toContain("128 files indexed");
    expect(view.container.querySelector('[role="progressbar"]')).toBeNull();
  });

  it("expands the files that need attention", async () => {
    const index = {
      stale: false,
      counts,
      current: null,
      error: null,
      problems: [{ rel_path: "bad.pdf", status: "failed", error: "cannot open PDF" }],
    };
    view = await mount(
      <IndexStatus
        collection={collection}
        index={index}
        disabled={false}
        onChangeFolder={vi.fn()}
        onRebuild={vi.fn()}
      />,
    );
    const pill = buttonByText(view.container, "1 file needs attention");
    expect(pill?.getAttribute("aria-expanded")).toBe("false");
    expect(view.container.textContent).not.toContain("bad.pdf");
    await click(pill);
    expect(pill?.getAttribute("aria-expanded")).toBe("true");
    expect(view.container.textContent).toContain("bad.pdf");
    expect(view.container.textContent).toContain("cannot open PDF");
    await click(pill);
    expect(view.container.textContent).not.toContain("bad.pdf");
  });

  it("counts several files that need attention", async () => {
    const problems = ["a.pdf", "b.docx", "c.txt"].map((rel_path) => ({
      rel_path,
      status: "failed",
      error: "x",
    }));
    const index = { stale: false, counts, current: null, error: null, problems };
    view = await mount(
      <IndexStatus
        collection={collection}
        index={index}
        disabled={false}
        onChangeFolder={vi.fn()}
        onRebuild={vi.fn()}
      />,
    );
    expect(buttonByText(view.container, "3 files need attention")).toBeDefined();
  });

  it("offers a rebuild when the index is stale", async () => {
    const onRebuild = vi.fn();
    const index = { stale: true, counts, current: null, error: null, problems: [] };
    view = await mount(
      <IndexStatus
        collection={collection}
        index={index}
        disabled={false}
        onChangeFolder={vi.fn()}
        onRebuild={onRebuild}
      />,
    );
    await click(buttonByText(view.container, "Rebuild index"));
    expect(onRebuild).toHaveBeenCalled();
  });

  it("opens the folder dialog from Change folder", async () => {
    const onChangeFolder = vi.fn();
    view = await mount(
      <IndexStatus
        collection={collection}
        index={null}
        disabled={false}
        onChangeFolder={onChangeFolder}
        onRebuild={vi.fn()}
      />,
    );
    await click(buttonByText(view.container, "Change folder"));
    expect(onChangeFolder).toHaveBeenCalled();
  });
});
