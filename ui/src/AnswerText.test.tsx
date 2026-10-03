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
    expect(onCite).toHaveBeenCalledWith(2);
  });

  it("shows markup from the model as text", async () => {
    const text = '<img src=x onerror="alert(1)">';
    view = await mount(<AnswerText text={text} sourceCount={0} onCite={() => {}} />);
    expect(view.container.querySelector("img")).toBeNull();
    expect(view.container.textContent).toBe(text);
  });
});
