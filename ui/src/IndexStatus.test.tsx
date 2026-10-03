import { afterEach, describe, expect, it, vi } from "vitest";
import IndexStatus from "./IndexStatus";
import { click, type Mounted, mount } from "./test-utils";

let view: Mounted | undefined;

afterEach(async () => {
  await view?.unmount();
  view = undefined;
});

const collection = { id: 1, name: "Contracts", folder_path: "C:/contracts", created_at: "t" };
const counts = { pending: 1, indexing: 1, indexed: 3, failed: 1, skipped: 0 };

describe("IndexStatus", () => {
  it("shows progress and the files that need attention", async () => {
    const index = {
      stale: false,
      counts,
      current: "b.pdf",
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
    const text = view.container.textContent ?? "";
    expect(text).toContain("3 of 6 files indexed · indexing b.pdf");
    expect(text).toContain("1 file(s) need attention");
    expect(text).toContain("bad.pdf");
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
    const buttons = [...view.container.querySelectorAll("button")];
    await click(buttons.find((b) => b.textContent === "Rebuild index"));
    expect(onRebuild).toHaveBeenCalled();
  });
});
