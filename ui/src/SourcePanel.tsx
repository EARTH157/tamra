import { ChevronLeft, ChevronRight, FileText, TriangleAlert, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { TextWithParagraphs } from "./AnswerText";
import { locateSource } from "./api";
import { type TranslationKey, useT } from "./i18n";
import { cpToUtf16 } from "./offsets";
import type { Locate, Match, Source, ViewerRequest } from "./types";

/** The file name of a collection-relative path ("a/b/c.pdf" -> "c.pdf"). */
export function fileName(path: string): string {
  return path.slice(path.lastIndexOf("/") + 1);
}

/** The folder part of a collection-relative path ("a/b/c.pdf" -> "a/b"; "" at the top). */
export function folderOf(path: string): string {
  const cut = path.lastIndexOf("/");
  return cut < 0 ? "" : path.slice(0, cut);
}

/** Where a source is: its page or section, then its folder ("p. 2 · Contracts/2026"). */
export function sourceLabel(source: Source): string {
  return [source.label, folderOf(source.file)].filter(Boolean).join(" · ");
}

/** A selection of an answer being checked against that answer's sources. */
export type Check = {
  /** A new number for every request: a panel state that belongs to one request is keyed by it. */
  id: number;
  messageId: number;
  /** The selected text: a raw substring of the message content. */
  selection: string;
  /** Where the selection lies in the message content; the answer text marks it. */
  start: number;
  end: number;
  n: number | null;
  status: "loading" | "ready" | "error";
  matches: Match[];
  /** Which of the matches is shown. */
  index: number;
  error: string | null;
};

type Props = {
  /** The answer's sources, to list when nothing matched. */
  sources: Source[];
  /** The answer's message id; null while the answer is still being streamed. */
  messageId: number | null;
  /** The selection being checked, or the source opened as it is (a source card, a passage). */
  check: Check | null;
  source: Source | null;
  onIndexChange: (index: number) => void;
  onOpenSource: (source: Source) => void;
  /** The "Open file" button: open the document viewer at the shown passage. */
  onOpenViewer: (request: ViewerRequest) => void;
  onClose: () => void;
};

type Translate = (key: TranslationKey, params?: Record<string, string | number>) => string;

/** The second line of the header: "page 14 of 32" for a located PDF page, else the saved label. */
function whereLine(match: Match, located: Locate | null, t: Translate): string {
  if (located?.found) {
    if (located.kind === "pdf" && located.page !== undefined && located.page_count !== undefined) {
      return t("source.pageOf", { page: located.page, count: located.page_count });
    }
    if (located.kind === "text" && located.start !== undefined && located.end !== undefined) {
      const last = located.end - 1;
      return located.start === last
        ? t("source.line", { n: located.start })
        : t("source.lines", { from: located.start, to: last });
    }
  }
  return match.location_label;
}

/**
 * The right-hand panel. For a checked selection it compares the selected answer text with the
 * passage of the source it matches (frame 2b), with the passage highlighted in the snapshot text
 * the answer saw. Opened from a source card it shows that snapshot as it is.
 */
export default function SourcePanel({
  sources,
  messageId,
  check,
  source,
  onIndexChange,
  onOpenSource,
  onOpenViewer,
  onClose,
}: Props) {
  const t = useT();
  const matches = check?.status === "ready" ? check.matches : [];
  const match = matches[check?.index ?? 0] ?? null;
  const located = useLocated(messageId, match);
  // Without the answer's sources there is no snapshot to offset into: show none.
  const snapshot = match ? (sources.find((s) => s.n === match.n)?.text ?? null) : null;

  const title = match ? match.file : (source?.file ?? null);
  const subtitle = match ? whereLine(match, located, t) : source ? sourceLabel(source) : "";
  const fileId = match ? match.file_id : (source?.file_id ?? null);
  const canOpen = messageId !== null && fileId !== null && fileId !== undefined && !!title;

  function openViewer() {
    if (!canOpen || messageId === null || fileId === null || fileId === undefined || !title) return;
    if (match) {
      onOpenViewer({
        messageId,
        n: match.n,
        fileId,
        file: title,
        start: match.start,
        end: match.end,
        selection: check?.selection ?? null,
      });
    } else if (source) {
      onOpenViewer({
        messageId,
        n: source.n,
        fileId,
        file: title,
        start: null,
        end: null,
        selection: null,
      });
    }
  }

  return (
    <aside className="source-panel" aria-label={t("chat.sourcePanel")}>
      <header>
        <FileText size={20} />
        <div className="source-title">
          <strong title={title ?? undefined}>{title ? fileName(title) : t("chat.sourcePanel")}</strong>
          {subtitle && <span>{subtitle}</span>}
        </div>
        {canOpen && (
          <button type="button" className="btn" onClick={openViewer}>
            {t("source.open")}
          </button>
        )}
        <button
          type="button"
          className="icon-button"
          aria-label={t("chat.closeSource")}
          onClick={onClose}
        >
          <X size={18} />
        </button>
      </header>
      {check && (
        <section className="compare" aria-label={t("source.compare")}>
          <h3 className="compare-title">{t("source.compare")}</h3>
          <div className="compare-row">
            <span className="compare-pill">{t("source.answer")}</span>
            <p className="compare-text">{check.selection}</p>
          </div>
          {match && (
            <>
              <div className="compare-row">
                <span className="compare-pill">{t("source.document")}</span>
                <p className="compare-text">{match.text}</p>
              </div>
              <div className="compare-bar">
                <span className={`match-badge ${match.label}`}>
                  {t(match.label === "strong" ? "source.strong" : "source.partial")}
                </span>
                {matches.length > 1 && (
                  <span className="match-nav">
                    <button
                      type="button"
                      className="icon-button"
                      aria-label={t("source.previous")}
                      disabled={check.index === 0}
                      onClick={() => onIndexChange(check.index - 1)}
                    >
                      <ChevronLeft size={16} />
                    </button>
                    <span>{t("source.matchOf", { index: check.index + 1, count: matches.length })}</span>
                    <button
                      type="button"
                      className="icon-button"
                      aria-label={t("source.next")}
                      disabled={check.index === matches.length - 1}
                      onClick={() => onIndexChange(check.index + 1)}
                    >
                      <ChevronRight size={16} />
                    </button>
                  </span>
                )}
              </div>
            </>
          )}
        </section>
      )}
      <div className="source-body">
        {check?.status === "loading" && (
          <p className="source-state" role="status">
            {t("source.checking")}
          </p>
        )}
        {check?.status === "error" && (
          <div className="chat-error" role="alert">
            <span>
              {t("source.failed")} {check.error}
            </span>
          </div>
        )}
        {check?.status === "ready" && !match && (
          <div className="source-none">
            <p className="source-state">{t("source.none")}</p>
            {sources.length > 0 && (
              <>
                <p className="source-state">{t("source.noneList")}</p>
                <ul className="source-cards">
                  {sources.map((s) => (
                    <li key={s.n}>
                      <button type="button" className="source-card" onClick={() => onOpenSource(s)}>
                        <span className="source-num">{s.n}</span>
                        <span className="source-meta">
                          <span className="source-file" title={s.file}>
                            {fileName(s.file)}
                          </span>
                          {s.label && <span className="source-label">{s.label}</span>}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </div>
        )}
        {match && snapshot !== null && (
          <>
            {match.changed && (
              <div className="source-warning" role="status">
                <TriangleAlert size={16} />
                <span>{t("source.changed")}</span>
              </div>
            )}
            <div className="paper">
              <Snapshot text={snapshot} start={match.start} end={match.end} />
            </div>
          </>
        )}
        {!check && source && (
          <div className="paper">
            <TextWithParagraphs text={source.text} />
          </div>
        )}
      </div>
    </aside>
  );
}

/** The snapshot text with the matched window (code-point offsets) highlighted and scrolled into view. */
function Snapshot({ text, start, end }: { text: string; start: number; end: number }) {
  const mark = useRef<HTMLElement>(null);
  // The core's offsets count code points; the string is indexed in UTF-16 units.
  const from = cpToUtf16(text, start);
  const to = Math.max(from, cpToUtf16(text, end));
  useEffect(() => {
    mark.current?.scrollIntoView?.({ block: "center" });
  }, [from, to, text]);
  return (
    <>
      {text.slice(0, from)}
      {to > from && (
        <mark className="snapshot-hit" ref={mark}>
          {text.slice(from, to)}
        </mark>
      )}
      {text.slice(to)}
    </>
  );
}

/**
 * Where the shown passage is in the current file; null until known, and when it cannot be told. A
 * result is only used for the passage it was asked for, so a step to the next match never shows
 * the page of the previous one for a frame.
 */
function useLocated(messageId: number | null, match: Match | null): Locate | null {
  const [result, setResult] = useState<{ key: string; located: Locate } | null>(null);
  const n = match?.n;
  const start = match?.start;
  const end = match?.end;
  const key = `${messageId}:${n}:${start}:${end}`;
  useEffect(() => {
    if (messageId === null || n === undefined || start === undefined || end === undefined) return;
    let current = true;
    locateSource(messageId, n, { start, end })
      .then((located) => {
        if (current) setResult({ key, located });
      })
      .catch(() => {
        // The header keeps the saved label.
      });
    return () => {
      current = false;
    };
  }, [messageId, n, start, end, key]);
  return result?.key === key ? result.located : null;
}
