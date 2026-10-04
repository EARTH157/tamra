import { splitCitations } from "./citations";

type Props = { text: string; sourceCount: number; onCite: (n: number) => void };

/** Answer text as plain text, with [n] markers as buttons that open source n. */
export default function AnswerText({ text, sourceCount, onCite }: Props) {
  return (
    <div className="answer-text">
      {splitCitations(text, sourceCount).map((segment, i) =>
        segment.kind === "text" ? (
          <span key={i}>{segment.text}</span>
        ) : (
          <button key={i} type="button" className="cite" onClick={() => onCite(segment.n)}>
            {segment.n}
          </button>
        ),
      )}
    </div>
  );
}
