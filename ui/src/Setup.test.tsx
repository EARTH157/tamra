import { afterEach, describe, expect, it, vi } from "vitest";
import Setup from "./Setup";
import { buttonByText, click, json, type Mounted, mockFetch, mount, press, typeInto } from "./test-utils";

let view: Mounted | undefined;

afterEach(async () => {
  await view?.unmount();
  view = undefined;
  vi.unstubAllGlobals();
});

const state = {
  collection: { id: 1, name: "docs", folder_path: "C:/docs", created_at: "t" },
  index: null,
};

describe("Setup", () => {
  it("indexes the typed folder", async () => {
    const calls = mockFetch({ "PUT /api/collection": () => json(state) });
    const onDone = vi.fn();
    view = await mount(<Setup onDone={onDone} onCancel={vi.fn()} />);
    await typeInto(view.container.querySelector("input"), "  C:/docs ");
    await click(view.container.querySelector('button[type="submit"]'));
    expect(calls).toEqual([
      { key: "PUT /api/collection", body: { name: "", folder_path: "C:/docs" } },
    ]);
    expect(onDone).toHaveBeenCalledWith(state);
  });

  it("shows why a folder was refused", async () => {
    mockFetch({ "PUT /api/collection": () => json({ detail: "Folder not found: C:/nope" }, 400) });
    view = await mount(<Setup onDone={vi.fn()} onCancel={vi.fn()} />);
    await typeInto(view.container.querySelector("input"), "C:/nope");
    await click(view.container.querySelector('button[type="submit"]'));
    expect(view.container.querySelector('[role="alert"]')?.textContent).toBe(
      "Folder not found: C:/nope",
    );
  });

  it("fills the path from the folder picker", async () => {
    mockFetch({ "POST /api/pick-folder": () => json({ folder_path: "D:/picked" }) });
    view = await mount(<Setup onDone={vi.fn()} onCancel={vi.fn()} />);
    await click(buttonByText(view.container, "Browse…"));
    expect(view.container.querySelector("input")?.value).toBe("D:/picked");
  });

  it("closes with Cancel or Esc", async () => {
    mockFetch({});
    const onCancel = vi.fn();
    view = await mount(<Setup onDone={vi.fn()} onCancel={onCancel} />);
    await click(buttonByText(view.container, "Cancel"));
    expect(onCancel).toHaveBeenCalledTimes(1);
    await press(view.container.querySelector("input"), "Escape");
    expect(onCancel).toHaveBeenCalledTimes(2);
  });

  it("closes with Esc even when focus has left the dialog", async () => {
    mockFetch({});
    const onCancel = vi.fn();
    view = await mount(<Setup onDone={vi.fn()} onCancel={onCancel} />);
    (document.activeElement as HTMLElement | null)?.blur();
    expect(view.container.querySelector('[role="dialog"]')?.contains(document.activeElement)).toBe(
      false,
    );
    await press(document.body, "Escape");
    expect(onCancel).toHaveBeenCalledTimes(1);
  });
});
