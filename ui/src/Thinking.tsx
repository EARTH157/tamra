import { ChevronDown, ChevronRight, Lightbulb } from "lucide-react";
import { useEffect, useState } from "react";
import { TextWithParagraphs } from "./AnswerText";
import { useT } from "./i18n";

/** The model's reasoning, kept only for this session: when it began and ended (ms), and its text. */
export type Thought = { text: string; startedAt: number; endedAt: number | null };

/** Whole seconds a thought took (or has taken so far). */
export function thoughtSeconds(thought: Thought, now: number = Date.now()): number {
  return Math.max(0, Math.round(((thought.endedAt ?? now) - thought.startedAt) / 1000));
}

/**
 * "Thinking… 12 s" with the reasoning streaming below it while the model thinks; "Thought for
 * 18 s" once it is done. It is open while live and collapsed afterwards, and a click toggles it.
 */
export default function Thinking({ thought }: { thought: Thought }) {
  const t = useT();
  const live = thought.endedAt === null;
  const [now, setNow] = useState(() => Date.now());
  const [manual, setManual] = useState<boolean | null>(null); // the user's choice, if any

  useEffect(() => {
    if (!live) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [live]);

  useEffect(() => {
    if (!live) setManual(null); // done thinking: back to collapsed
  }, [live]);

  const open = manual ?? live;
  const seconds = thoughtSeconds(thought, now);
  return (
    <div className={live ? "thinking live" : "thinking"}>
      <button
        type="button"
        className="thinking-toggle"
        aria-expanded={open}
        onClick={() => setManual(!open)}
      >
        <Lightbulb size={16} />
        {live ? (
          <>
            <span className="thinking-title">{t("chat.thinking")}</span>
            <span className="thinking-time">{t("chat.seconds", { seconds })}</span>
          </>
        ) : (
          <span className="thinking-title">{t("chat.thought", { seconds })}</span>
        )}
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
      </button>
      {open && (
        <div className="thinking-body">
          <TextWithParagraphs text={thought.text} />
        </div>
      )}
    </div>
  );
}
