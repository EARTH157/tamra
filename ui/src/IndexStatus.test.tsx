import { afterEach, describe, expect, it, vi } from "vitest";
import IndexStatus from "./IndexStatus";
import { buttonByText, click, type Mounted, mount } from "./test-utils";
import type { IndexStatus as IndexStatusType, Project } from "./types";

let view: Mounted | undefined;

afterEach(async () => {
  await view?.unmount();
  view = undefined;
});

const counts = { pending: 1, indexing: 1, indexed: 3, failed: 1, skipped: 0 };
const idle = { stale: false, counts, current: null, error: null, problems: [] };
const project = (index: IndexStatusType = idle): Project => ({
  id: 1,
  name: "Contracts",
  folder_path: "C:/contracts",
  index,
});

describe("IndexStatus", () => {
  it("shows progress while indexing", async () => {
    const index = { stale: false, counts, current: "b.pdf", error: null, problems: [] };
    view = await mount(
      <IndexStatus
        project={project(index)}
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
        project={project(index)}
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
        project={project(index)}
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
        project={project(index)}
        disabled={false}
        onChangeFolder={vi.fn()}
        onRebuild={vi.fn()}
      />,
    );
    expect(buttonByText(view.container, "3 files need attention")).toBeDefined();
  });

  it("says which page OCR is reading", async () => {
    const index = { ...idle, current: "scan.pdf", reading: { page: 3, pages: 40 } };
    view = await mount(
      <IndexStatus
        project={project(index)}
        disabled={false}
        onChangeFolder={vi.fn()}
        onRebuild={vi.fn()}
      />,
    );
    expect(view.container.textContent).toContain("Indexing scan.pdf");
    expect(view.container.textContent).toContain("Reading scanned page 3 of 40");
  });

  it("explains a scan that waits for the OCR model and links to the AI model settings", async () => {
    const onOpenModelSettings = vi.fn();
    const problems = [
      {
        rel_path: "scan.pdf",
        status: "indexed",
        error: "no text layer on page(s) 2 (needs OCR)",
        needs_ocr: true,
      },
    ];
    view = await mount(
      <IndexStatus
        project={project({ ...idle, problems })}
        disabled={false}
        onChangeFolder={vi.fn()}
        onRebuild={vi.fn()}
        onOpenModelSettings={onOpenModelSettings}
      />,
    );
    await click(buttonByText(view.container, "1 file needs attention"));
    const item = view.container.querySelector<HTMLElement>(".problems li")!;
    expect(item.textContent).toContain("Scanned pages need the text recognition (OCR) model.");
    const open = buttonByText(item, "Open model settings")!;
    expect(open.getAttribute("aria-label")).toBe("Open model settings: scan.pdf");
    await click(open);
    expect(onOpenModelSettings).toHaveBeenCalled();
  });

  it("offers a rebuild when the index is stale", async () => {
    const onRebuild = vi.fn();
    const index = { stale: true, counts, current: null, error: null, problems: [] };
    view = await mount(
      <IndexStatus
        project={project(index)}
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
        project={project()}
        disabled={false}
        onChangeFolder={onChangeFolder}
        onRebuild={vi.fn()}
      />,
    );
    await click(buttonByText(view.container, "Change folder"));
    expect(onChangeFolder).toHaveBeenCalled();
  });

  it("explains a pause for the missing search model, with a button to the model settings", async () => {
    const onOpenModelSettings = vi.fn();
    const index = {
      stale: false,
      counts,
      current: null,
      error: "Embedding model unavailable: model.onnx not found",
      problems: [],
    };
    view = await mount(
      <IndexStatus
        project={project(index)}
        disabled={false}
        onChangeFolder={vi.fn()}
        onRebuild={vi.fn()}
        onOpenModelSettings={onOpenModelSettings}
      />,
    );
    const text = view.container.textContent ?? "";
    expect(text).toContain("The search model is not installed. Download it in Settings > AI model.");
    expect(text).not.toContain("model.onnx");
    await click(buttonByText(view.container, "Open model settings"));
    expect(onOpenModelSettings).toHaveBeenCalled();
  });

  it("shows any other index error as the core worded it", async () => {
    const index = { stale: false, counts, current: null, error: "Folder not found: C:/x", problems: [] };
    view = await mount(
      <IndexStatus
        project={project(index)}
        disabled={false}
        onChangeFolder={vi.fn()}
        onRebuild={vi.fn()}
        onOpenModelSettings={vi.fn()}
      />,
    );
    expect(view.container.textContent).toContain("Folder not found: C:/x");
    expect(buttonByText(view.container, "Open model settings")).toBeUndefined();
  });
});
