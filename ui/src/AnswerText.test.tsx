import { afterEach, describe, expect, it, vi } from "vitest";
import AnswerText from "./AnswerText";
import { click, type Mounted, mount } from "./test-utils";

let view: Mounted | undefined;

afterEach(async () => {
  await view?.unmount();
  view = undefined;
});

describe("AnswerText", () => {
  it("renders [n] markers as buttons that open the source", async () => {
    const onCite = vi.fn();
    view = await mount(<AnswerText text="Three years [2]." sourceCount={2} onCite={onCite} />);
    const button = view.container.querySelector("button.cite");
    expect(button?.textContent).toBe("2");
    expect(view.container.textContent).toBe("Three years 2.");
    await click(button);
    expect(onCite).toHaveBeenCalledWith(2, 12);
  });

  it("shows markup from the model as text", async () => {
    const text = '<img src=x onerror="alert(1)">';
    view = await mount(<AnswerText text={text} sourceCount={0} onCite={() => {}} />);
    expect(view.container.querySelector("img")).toBeNull();
    expect(view.container.textContent).toBe(text);
  });

  it("turns a blank line into a paragraph gap without dropping text", async () => {
    view = await mount(<AnswerText text={"One [1].\n\nTwo."} sourceCount={1} onCite={() => {}} />);
    expect(view.container.querySelectorAll(".para-break").length).toBe(1);
    expect(view.container.textContent).toBe("One 1.Two.");
  });

  it("marks the chips of the open source", async () => {
    view = await mount(
      <AnswerText text="A [1] B [2] C [1]" sourceCount={2} onCite={() => {}} active={1} />,
    );
    const chips = [...view.container.querySelectorAll("button.cite")];
    expect(chips.map((chip) => chip.classList.contains("active"))).toEqual([true, false, true]);
  });
});
