import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./api";
import Setup from "./Setup";
import { buttonByText, click, json, type Mounted, mockFetch, mount, press, typeInto } from "./test-utils";

let view: Mounted | undefined;

afterEach(async () => {
  await view?.unmount();
  view = undefined;
  vi.unstubAllGlobals();
});

describe("Setup", () => {
  it("starts from the project's folder and hands over the typed folder, trimmed", async () => {
    const onSubmit = vi.fn(async () => {});
    view = await mount(<Setup onSubmit={onSubmit} onCancel={vi.fn()} initialFolder="C:/old" />);
    expect(view.container.querySelector("input")?.value).toBe("C:/old");
    await typeInto(view.container.querySelector("input"), "  C:/docs ");
    await click(view.container.querySelector('button[type="submit"]'));
    expect(onSubmit).toHaveBeenCalledWith("C:/docs");
  });

  it("will not submit an empty folder", async () => {
    view = await mount(<Setup onSubmit={vi.fn()} onCancel={vi.fn()} />);
    const submit = view.container.querySelector<HTMLButtonElement>('button[type="submit"]');
    expect(submit?.disabled).toBe(true);
  });

  it("shows why a folder was refused, in the user's words", async () => {
    const onSubmit = vi.fn(async () => {
      throw new ApiError(400, "Folder not found.");
    });
    view = await mount(<Setup onSubmit={onSubmit} onCancel={vi.fn()} />);
    await typeInto(view.container.querySelector("input"), "C:/nope");
    await click(view.container.querySelector('button[type="submit"]'));
    expect(view.container.querySelector('[role="alert"]')?.textContent).toBe(
      "That folder does not exist.",
    );
    expect(view.container.querySelector<HTMLButtonElement>('button[type="submit"]')?.disabled).toBe(
      false,
    ); // the user can fix the path and try again
  });

  it("fills the path from the folder picker", async () => {
    mockFetch({ "POST /api/pick-folder": () => json({ folder_path: "D:/picked" }) });
    view = await mount(<Setup onSubmit={vi.fn()} onCancel={vi.fn()} />);
    await click(buttonByText(view.container, "Browse…"));
    expect(view.container.querySelector("input")?.value).toBe("D:/picked");
  });

  it("keeps the path when the picker is cancelled", async () => {
    mockFetch({ "POST /api/pick-folder": () => json({ folder_path: null }) });
    view = await mount(<Setup onSubmit={vi.fn()} onCancel={vi.fn()} initialFolder="C:/keep" />);
    await click(buttonByText(view.container, "Browse…"));
    expect(view.container.querySelector("input")?.value).toBe("C:/keep");
  });

  it("closes with Cancel or Esc", async () => {
    mockFetch({});
    const onCancel = vi.fn();
    view = await mount(<Setup onSubmit={vi.fn()} onCancel={onCancel} />);
    await click(buttonByText(view.container, "Cancel"));
    expect(onCancel).toHaveBeenCalledTimes(1);
    await press(view.container.querySelector("input"), "Escape");
    expect(onCancel).toHaveBeenCalledTimes(2);
  });

  it("closes with Esc even when focus has left the dialog", async () => {
    mockFetch({});
    const onCancel = vi.fn();
    view = await mount(<Setup onSubmit={vi.fn()} onCancel={onCancel} />);
    (document.activeElement as HTMLElement | null)?.blur();
    expect(view.container.querySelector('[role="dialog"]')?.contains(document.activeElement)).toBe(
      false,
    );
    await press(document.body, "Escape");
    expect(onCancel).toHaveBeenCalledTimes(1);
  });
});
