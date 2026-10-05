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
