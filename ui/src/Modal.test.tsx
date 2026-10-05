import { act } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import Modal from "./Modal";
import { type Mounted, mount } from "./test-utils";

let view: Mounted | undefined;

afterEach(async () => {
  await view?.unmount();
  view = undefined;
});

/** Press Tab; returns the event, to see whether the dialog took it over. */
async function tab(shift = false): Promise<KeyboardEvent> {
  const event = new KeyboardEvent("keydown", {
    key: "Tab",
    shiftKey: shift,
    bubbles: true,
    cancelable: true,
  });
  await act(async () => {
    document.dispatchEvent(event);
  });
  return event;
}

const dialog = () => (
  <>
    <button type="button" id="outside">
      outside
    </button>
    <Modal labelledBy="t" onClose={vi.fn()}>
      <h2 id="t">Title</h2>
      <button type="button" id="a">
        a
      </button>
      <button type="button" id="b" disabled>
        b
      </button>
      <button type="button" id="c">
        c
      </button>
    </Modal>
  </>
);

const get = (id: string) => document.getElementById(id) as HTMLElement;

describe("Modal focus", () => {
  it("wraps Tab from the last control to the first, and Shift+Tab back to the last", async () => {
    view = await mount(dialog());
    expect(document.activeElement).toBe(get("a"));
    get("c").focus();
    await tab();
    expect(document.activeElement).toBe(get("a"));
    await tab(true);
    expect(document.activeElement).toBe(get("c"));
  });

  it("leaves Tab alone between the controls, and pulls focus that left the dialog back in", async () => {
    view = await mount(dialog());
    // From "a" the browser moves on by itself (a disabled control is skipped).
    expect((await tab()).defaultPrevented).toBe(false);
    get("outside").focus();
    await tab();
    expect(document.activeElement).toBe(get("a"));
    get("outside").focus();
    await tab(true);
    expect(document.activeElement).toBe(get("c"));
  });
});
