import { Fragment } from "react";
import { splitCitations } from "./citations";
import { useT } from "./i18n";

type Props = {
  text: string;
  sourceCount: number;
  onCite: (n: number) => void;
  /** The citation whose source is open; its chips are drawn filled. */
  active?: number | null;
};

const BLANK_LINE = /\n[ \t]*\n\s*/;

/** Plain text in which a blank line becomes a short paragraph gap. */
export function TextWithParagraphs({ text }: { text: string }) {
  const parts = text.split(BLANK_LINE);
  return parts.map((part, i) => (
    <Fragment key={i}>
      {i > 0 && <span className="para-break" />}
      <span>{part}</span>
    </Fragment>
  ));
}

/** Answer text as plain text, with [n] markers as buttons that open source n. */
export default function AnswerText({ text, sourceCount, onCite, active = null }: Props) {
  const t = useT();
  return (
    <div className="answer-text">
      {splitCitations(text, sourceCount).map((segment, i) =>
        segment.kind === "text" ? (
          <TextWithParagraphs key={i} text={segment.text} />
        ) : (
          <button
            key={i}
            type="button"
            className={segment.n === active ? "cite active" : "cite"}
            aria-label={t("chat.cite", { n: segment.n })}
            aria-pressed={segment.n === active}
            onClick={() => onCite(segment.n)}
          >
            {segment.n}
          </button>
        ),
      )}
    </div>
  );
}
