import { act, useState } from "react";
import { afterEach, describe, expect, it } from "vitest";
import Splitter, { SPLITTER_STEP } from "./Splitter";
import { type Mounted, mount, press } from "./test-utils";

let view: Mounted | undefined;

afterEach(async () => {
  await view?.unmount();
  view = undefined;
});

function Sized({ grow, orientation }: { grow: 1 | -1; orientation: "vertical" | "horizontal" }) {
  const [size, setSize] = useState(300);
  return (
    <>
      <Splitter label="Resize" orientation={orientation} size={size} min={200} max={() => 400} grow={grow} onResize={setSize} />
      <output>{size}</output>
    </>
  );
}

const bar = () => view!.container.querySelector<HTMLElement>('[role="separator"]')!;
const size = () => Number(view!.container.querySelector("output")?.textContent);

async function pointer(type: string, at: number) {
  await act(async () => {
    bar().dispatchEvent(new MouseEvent(type, { bubbles: true, button: 0, clientX: at, clientY: at }));
  });
}

describe("Splitter", () => {
  it("is a focusable separator that says its size and range", async () => {
    view = await mount(<Sized grow={1} orientation="vertical" />);
    expect(bar().getAttribute("aria-label")).toBe("Resize");
    expect(bar().getAttribute("aria-orientation")).toBe("vertical");
    expect(bar().tabIndex).toBe(0);
    expect([bar().getAttribute("aria-valuenow"), bar().getAttribute("aria-valuemin"), bar().getAttribute("aria-valuemax")])
      .toEqual(["300", "200", "400"]);
  });

  it("follows a drag, within its range, and stops at the release", async () => {
    view = await mount(<Sized grow={1} orientation="vertical" />);
    await pointer("pointerdown", 100);
    await pointer("pointermove", 150);
    expect(size()).toBe(350);
    await pointer("pointermove", 400);
    expect(size()).toBe(400); // the largest
    await pointer("pointermove", -500);
    expect(size()).toBe(200); // the smallest
    await pointer("pointerup", -500);
    await pointer("pointermove", 120);
    expect(size()).toBe(200);
  });

  it("grows the other way for a pane on the far side of the bar", async () => {
    view = await mount(<Sized grow={-1} orientation="vertical" />);
    await pointer("pointerdown", 100);
    await pointer("pointermove", 60); // dragged left: the right-hand pane gets wider
    expect(size()).toBe(340);
    await press(bar(), "ArrowLeft");
    expect(size()).toBe(340 + SPLITTER_STEP);
  });

  it("moves with the arrow keys of its orientation, and Home and End", async () => {
    view = await mount(<Sized grow={1} orientation="horizontal" />);
    await press(bar(), "ArrowDown");
    expect(size()).toBe(300 + SPLITTER_STEP);
    await press(bar(), "ArrowUp");
    await press(bar(), "ArrowUp");
    expect(size()).toBe(300 - SPLITTER_STEP);
    await press(bar(), "ArrowLeft"); // not this bar's direction
    expect(size()).toBe(300 - SPLITTER_STEP);
    await press(bar(), "End");
    expect(size()).toBe(400);
    await press(bar(), "Home");
    expect(size()).toBe(200);
  });
});
