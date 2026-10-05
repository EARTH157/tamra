import { Fragment, type Ref } from "react";
import { splitCitations } from "./citations";
import { useT } from "./i18n";

/** A range of the answer text, in offsets of its content. */
export type Highlight = { start: number; end: number };

type Props = {
  text: string;
  sourceCount: number;
  /** Called with the source number and the offset in `text` where the clicked marker starts. */
  onCite: (n: number, index: number) => void;
  /** The citation whose source is open; its chips are drawn filled. */
  active?: number | null;
  /** The span being checked, drawn with the highlight colour. */
  highlight?: Highlight | null;
  /** The element that holds the rendered text, for mapping a selection back to `text`. */
  ref?: Ref<HTMLDivElement>;
};

const BLANK_LINE = /\n[ \t]*\n\s*/g;

/** The paragraphs of a text with their offsets; a blank line is the break between two. */
function paragraphs(text: string, offset: number): { text: string; start: number }[] {
  const parts: { text: string; start: number }[] = [];
  let last = 0;
  for (const match of text.matchAll(BLANK_LINE)) {
    const at = match.index ?? 0;
    parts.push({ text: text.slice(last, at), start: offset + last });
    last = at + match[0].length;
  }
  parts.push({ text: text.slice(last), start: offset + last });
  return parts;
}

/**
 * Plain text in which a blank line becomes a short paragraph gap. With `offset` (where the text
 * starts in the answer) every span records its offsets, so a selection can be mapped back; the
 * part inside `highlight` is then marked.
 */
export function TextWithParagraphs({
  text,
  offset,
  highlight,
}: {
  text: string;
  offset?: number;
  highlight?: Highlight | null;
}) {
  return paragraphs(text, offset ?? 0).map((part, i) => (
    <Fragment key={i}>
      {i > 0 && <span className="para-break" />}
      {offset === undefined ? (
        <span>{part.text}</span>
      ) : (
        <Pieces text={part.text} start={part.start} highlight={highlight ?? null} />
      )}
    </Fragment>
  ));
}

/** One paragraph, cut where the highlight begins and ends. */
function Pieces({ text, start, highlight }: { text: string; start: number; highlight: Highlight | null }) {
  const end = start + text.length;
  const from = highlight ? Math.min(Math.max(highlight.start, start), end) : end;
  const to = highlight ? Math.min(Math.max(highlight.end, start), end) : end;
  if (from >= to) {
    return (
      <span data-start={start} data-end={end}>
        {text}
      </span>
    );
  }
  return (
    <>
      {from > start && (
        <span data-start={start} data-end={from}>
          {text.slice(0, from - start)}
        </span>
      )}
      <mark className="answer-hit" data-start={from} data-end={to}>
        {text.slice(from - start, to - start)}
      </mark>
      {to < end && (
        <span data-start={to} data-end={end}>
          {text.slice(to - start)}
        </span>
      )}
    </>
  );
}

/** Answer text as plain text, with [n] markers as buttons that open source n. */
export default function AnswerText({
  text,
  sourceCount,
  onCite,
  active = null,
  highlight = null,
  ref,
}: Props) {
  const t = useT();
  return (
    <div className="answer-text" ref={ref}>
      {splitCitations(text, sourceCount).map((segment, i) =>
        segment.kind === "text" ? (
          <TextWithParagraphs
            key={i}
            text={segment.text}
            offset={segment.start}
            highlight={highlight}
          />
        ) : (
          <button
            key={i}
            type="button"
            className={segment.n === active ? "cite active" : "cite"}
            aria-label={t("chat.cite", { n: segment.n })}
            aria-pressed={segment.n === active}
            data-start={segment.start}
            data-end={segment.end}
            data-cite={segment.n}
            onClick={() => onCite(segment.n, segment.start)}
          >
            {segment.n}
          </button>
        ),
      )}
    </div>
  );
}
