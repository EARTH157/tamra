import { afterEach, describe, expect, it } from "vitest";
import AnswerText from "./AnswerText";
import { selectionInside, sentenceBefore } from "./selection";
import { type Mounted, mount } from "./test-utils";

let view: Mounted | undefined;

afterEach(async () => {
  window.getSelection()?.removeAllRanges();
  await view?.unmount();
  view = undefined;
});

/** Two answers, each in a .message like the chat's. */
async function mountAnswers(first: string, second = "Another answer.") {
  view = await mount(
    <>
      <div className="message" id="a">
        <AnswerText text={first} sourceCount={3} onCite={() => {}} />
      </div>
      <div className="message" id="b">
        <AnswerText text={second} sourceCount={3} onCite={() => {}} />
      </div>
    </>,
  );
  const root = (id: string) => view!.container.querySelector<HTMLElement>(`#${id} .answer-text`)!;
  return { a: root("a"), b: root("b") };
}

/** Select from (node, offset) to (node, offset). */
function select(from: [Node, number], to: [Node, number]): void {
  const range = document.createRange();
  range.setStart(...from);
  range.setEnd(...to);
  const selection = window.getSelection()!;
  selection.removeAllRanges();
  selection.addRange(range);
}

const textOf = (root: HTMLElement, i: number) => root.querySelectorAll("span[data-start]")[i].firstChild!;

describe("selectionInside", () => {
  it("maps a selection inside plain text to content offsets", async () => {
    const content = "Three years [1] and more.";
    const { a } = await mountAnswers(content);
    select([textOf(a, 0), 6], [textOf(a, 0), 11]);
    expect(selectionInside(a, content)).toEqual({ text: "years", start: 6, end: 11 });
  });

  it("maps a selection that spans a chip through the marker's real length", async () => {
    const content = "Three years [1] and more.";
    const { a } = await mountAnswers(content);
    const after = textOf(a, 1); // " and more."
    select([textOf(a, 0), 6], [after, 4]);
    // The chip shows "1" but the marker "[1]" is three characters of the content.
    expect(selectionInside(a, content)).toEqual({
      text: "years [1] and",
      start: 6,
      end: 19,
    });
  });

  it("includes a chip that holds the end of the selection and a comma list", async () => {
    const content = "Claim [1, 2]. Next.";
    const { a } = await mountAnswers(content);
    const chip = a.querySelector('button[data-cite="2"]')!;
    select([textOf(a, 0), 0], [chip.firstChild!, 1]);
    expect(selectionInside(a, content)).toEqual({ text: "Claim [1, 2]", start: 0, end: 12 });
  });

  it("counts the paragraph breaks that are not rendered", async () => {
    const content = "First part.\n\nSecond part.";
    const { a } = await mountAnswers(content);
    select([textOf(a, 0), 6], [textOf(a, 1), 6]);
    expect(selectionInside(a, content)).toEqual({
      text: "part.\n\nSecond",
      start: 6,
      end: 19,
    });
  });

  it("maps a boundary on the container itself", async () => {
    const content = "Three years [1] and more.";
    const { a } = await mountAnswers(content);
    select([a, 0], [a, a.childNodes.length]);
    expect(selectionInside(a, content)).toEqual({ text: content, start: 0, end: content.length });
  });

  it("trims the selection and drops one that is too short or too long", async () => {
    const content = "  Hello world  ";
    const { a } = await mountAnswers(content);
    select([textOf(a, 0), 0], [textOf(a, 0), content.length]);
    expect(selectionInside(a, content)).toEqual({ text: "Hello world", start: 2, end: 13 });
    select([textOf(a, 0), 2], [textOf(a, 0), 3]);
    expect(selectionInside(a, content)).toBeNull();
  });

  it("drops a selection over 2000 characters", async () => {
    const long = "x".repeat(2001);
    const { a } = await mountAnswers(long);
    select([textOf(a, 0), 0], [textOf(a, 0), 2000]);
    expect(selectionInside(a, long)?.end).toBe(2000);
    select([textOf(a, 0), 0], [textOf(a, 0), 2001]);
    expect(selectionInside(a, long)).toBeNull();
  });

  it("is null when nothing is selected", async () => {
    const { a } = await mountAnswers("Hello world");
    expect(selectionInside(a, "Hello world")).toBeNull();
    select([textOf(a, 0), 3], [textOf(a, 0), 3]);
    expect(selectionInside(a, "Hello world")).toBeNull();
  });

  it("is null across two messages", async () => {
    const content = "Hello world";
    const { a, b } = await mountAnswers(content, "Another answer.");
    select([textOf(a, 0), 2], [textOf(b, 0), 5]);
    expect(selectionInside(a, content)).toBeNull();
    expect(selectionInside(b, "Another answer.")).toBeNull();
  });

  it("maps Thai text, which has no spaces to split on", async () => {
    const content = "พนักงานใหม่ลาพักร้อนได้ 6 วัน [1] ส่วนอื่นได้ 10 วัน";
    const { a } = await mountAnswers(content);
    const first = textOf(a, 0);
    select([first, 6], [first, 22]);
    expect(selectionInside(a, content)).toEqual({
      text: content.slice(6, 22),
      start: 6,
      end: 22,
    });
    select([first, 3], [textOf(a, 1), 5]);
    const picked = selectionInside(a, content);
    expect(picked?.text).toBe(content.slice(3, content.indexOf("[1]") + 3 + 5));
    expect(picked?.text).toContain("[1]");
  });

  it("gives the same offsets for a selection made backwards", async () => {
    const content = "Three years [1] and more.";
    const { a } = await mountAnswers(content);
    window
      .getSelection()!
      .setBaseAndExtent(textOf(a, 1), 4, textOf(a, 0), 6); // anchor after focus
    expect(window.getSelection()!.anchorNode).toBe(textOf(a, 1));
    expect(selectionInside(a, content)).toEqual({ text: "years [1] and", start: 6, end: 19 });
  });

  it("maps a boundary in an empty paragraph break to the next text", async () => {
    const content = "First part.\n\nSecond part.";
    const { a } = await mountAnswers(content);
    const gap = a.querySelector(".para-break")!;
    select([gap, 0], [textOf(a, 1), 6]);
    expect(selectionInside(a, content)).toEqual({ text: "Second", start: 13, end: 19 });
  });

  it("takes a triple click on the last paragraph, which ends at the start of the next block", async () => {
    const content = "First part.\n\nSecond part.";
    const { a, b } = await mountAnswers(content, "Another answer.");
    select([textOf(a, 1), 0], [textOf(b, 0), 0]);
    expect(selectionInside(a, content)).toEqual({
      text: "Second part.",
      start: 13,
      end: 25,
    });
    select([textOf(a, 1), 0], [b.closest(".message")!, 0]);
    expect(selectionInside(a, content)?.text).toBe("Second part.");
    // The next block's start is no excuse to reach further in.
    select([textOf(a, 1), 0], [textOf(b, 0), 3]);
    expect(selectionInside(a, content)).toBeNull();
  });

  it("is null when the selection starts before the answer text, in the thinking block", async () => {
    const content = "Hello world";
    view = await mount(
      <div className="message">
        <p className="thinking">Let me think about this.</p>
        <AnswerText text={content} sourceCount={0} onCite={() => {}} />
      </div>,
    );
    const root = view.container.querySelector<HTMLElement>(".answer-text")!;
    const thinking = view.container.querySelector(".thinking")!.firstChild!;
    select([thinking, 3], [textOf(root, 0), 5]);
    expect(selectionInside(root, content)).toBeNull();
  });

  it("reaches to the end of the text when the selection runs past it inside the message", async () => {
    const content = "Hello world";
    const { a } = await mountAnswers(content);
    const message = a.closest(".message")!;
    select([textOf(a, 0), 6], [message, message.childNodes.length]);
    expect(selectionInside(a, content)).toEqual({ text: "world", start: 6, end: 11 });
  });
});

describe("sentenceBefore", () => {
  const sentence = (content: string, marker: string, nth = 0) => {
    let at = -1;
    for (let i = 0; i <= nth; i++) at = content.indexOf(marker, at + 1);
    const { start, end } = sentenceBefore(content, at);
    return content.slice(start, end);
  };

  it("finds the sentence before chip 1 and chip 2 in English", () => {
    const content = "Staff get 6 days. New hires wait 119 days [1]. Part-timers get 3 days [2].";
    expect(sentence(content, "[1]")).toBe("New hires wait 119 days");
    expect(sentence(content, "[2]")).toBe("Part-timers get 3 days");
  });

  it("keeps the full stop when the chip follows it", () => {
    const content = "Staff get 6 days. New hires wait. [1] Others stay.";
    expect(sentence(content, "[1]")).toBe("New hires wait.");
  });

  it("does not end a sentence at a decimal point", () => {
    expect(sentence("Leave is 6.5 days a year [1].", "[1]")).toBe("Leave is 6.5 days a year");
  });

  it("finds the sentence before chip 1 and chip 2 in Thai, where a marker ends a sentence", () => {
    const content = "พนักงานใหม่มีสิทธิ์ลาพักร้อนได้ 6 วันต่อปี [1] ส่วนพนักงานที่ทำงานครบ 3 ปีขึ้นไปจะได้รับวันลาเพิ่มเป็น 10 วันต่อปี [2]";
    expect(sentence(content, "[1]")).toBe("พนักงานใหม่มีสิทธิ์ลาพักร้อนได้ 6 วันต่อปี");
    expect(sentence(content, "[2]")).toBe(
      "ส่วนพนักงานที่ทำงานครบ 3 ปีขึ้นไปจะได้รับวันลาเพิ่มเป็น 10 วันต่อปี",
    );
  });

  it("ends a sentence at a line break and at a Chinese full stop", () => {
    expect(sentence("วันลาพักร้อน\nไม่เกิน 5 วัน [1]", "[1]")).toBe("ไม่เกิน 5 วัน");
    expect(sentence("试用期三个月。年假五天[1]。", "[1]")).toBe("年假五天");
  });

  it("looks past the markers just before the chip", () => {
    const content = "The term is three years [1][2]";
    expect(sentence(content, "[2]")).toBe("The term is three years");
    expect(sentence(content, "[1]")).toBe("The term is three years");
  });

  it("is empty when no text comes before the chip, and is capped at 2000 characters", () => {
    expect(sentenceBefore("[1] Text.", 0)).toEqual({ start: 0, end: 0 });
    const long = `${"word ".repeat(600)}end [1]`;
    const { start, end } = sentenceBefore(long, long.indexOf("[1]"));
    expect(end - start).toBeLessThanOrEqual(2000);
    expect(long.slice(start, end).endsWith("end")).toBe(true);
  });
});
