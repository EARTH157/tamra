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
      <AnswerText text="A [1] B [2] C [1]" sourceCount={2} onCite={() => {}} active={[1]} />,
    );
    const chips = [...view.container.querySelectorAll("button.cite")];
    expect(chips.map((chip) => chip.classList.contains("active"))).toEqual([true, false, true]);
  });

  it("renders a formula as math, keeps text and chips around it", async () => {
    const text = String.raw`Speed is $ \frac{\pi}{4} $ rad/s [1] and \(x^2\)[2].`;
    view = await mount(<AnswerText text={text} sourceCount={2} onCite={() => {}} />);
    const math = view.container.querySelectorAll("span.math");
    expect(math).toHaveLength(2);
    expect(math[0].querySelector("math mfrac")).not.toBeNull();
    // The carrier spans the raw source, delimiters included.
    expect(math[0].getAttribute("data-start")).toBe(String(text.indexOf("$")));
    expect(math[0].getAttribute("data-end")).toBe(String(text.indexOf("$ rad") + 1));
    expect([...view.container.querySelectorAll("button.cite")].map((b) => b.textContent)).toEqual(["1", "2"]);
    expect(view.container.querySelector("button.cite[data-start]")?.getAttribute("data-start")).toBe(
      String(text.indexOf("[1]")),
    );
  });

  it("shows markup inside a formula as text, never as elements", async () => {
    const text = "$<script>alert(1)</script> x^2$ and $<img src=x onerror=alert(2)> y_1$";
    view = await mount(<AnswerText text={text} sourceCount={0} onCite={() => {}} />);
    expect(view.container.querySelector("script")).toBeNull();
    expect(view.container.querySelector("img")).toBeNull();
    expect(view.container.querySelectorAll("math")).toHaveLength(2);
  });

  it("marks a formula inside the highlighted span", async () => {
    const text = "Before $x^2$ after";
    const start = text.indexOf("$");
    view = await mount(
      <AnswerText text={text} sourceCount={0} onCite={() => {}} highlight={{ start, end: start + 5 }} />,
    );
    expect(view.container.querySelector("span.math.answer-hit")).not.toBeNull();
  });

  it("keeps a half-written formula as text while streaming", async () => {
    view = await mount(<AnswerText text={String.raw`Speed is $\frac{`} sourceCount={0} onCite={() => {}} />);
    expect(view.container.querySelector("span.math")).toBeNull();
    expect(view.container.textContent).toBe(String.raw`Speed is $\frac{`);
  });
});
