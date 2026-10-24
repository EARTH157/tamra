import { ChevronLeft, ChevronRight, FileText, Minus, Plus, TriangleAlert, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { MathText, TextWithParagraphs } from "./AnswerText";
import { locateSource } from "./api";
import { MARKER_SOURCE } from "./citations";
import { type TranslationKey, useT } from "./i18n";
import { cpToUtf16 } from "./offsets";
import PdfPage, { useDragScroll, useWheelZoom } from "./PdfPage";
import { MAX_SELECTION } from "./selection";
import Splitter from "./Splitter";
import type { Locate, Match, Source, ViewerRequest } from "./types";

/** The zoom steps of the panel's PDF page, in percent of the panel width. */
export const PANEL_ZOOM_STEPS = [100, 125, 150, 200, 300];

/** How short Compare can be dragged, in CSS pixels. */
const COMPARE_MIN = 100;

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

/** Selected answer text as it can be found in a passage: without its [n] citations. */
export function answerFocus(selection: string | null | undefined): string | undefined {
  return selection?.replace(new RegExp(MARKER_SOURCE, "g"), " ").trim() || undefined;
}

/** True when an answer's sources come from more than one project: then each names its project. */
export function spansProjects(sources: Source[]): boolean {
  return new Set(sources.map((source) => source.project).filter(Boolean)).size > 1;
}

/** The project's notes as a source of an answer (project notes spec): no file, no location. */
export function isProjectNotes(source: Pick<Source, "location">): boolean {
  return source.location?.kind === "notes";
}

/** The name shown for a source's file: "HR Documents · lease.pdf" when the project is named;
 * notesName for the project's notes. */
export function shownFile(source: Source, withProject: boolean, notesName: string): string {
  if (isProjectNotes(source)) return notesName;
  const name = fileName(source.file);
  return withProject && source.project ? `${source.project} · ${name}` : name;
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
  /** The sources span several projects, so each file is shown with its project. */
  multiProject?: boolean;
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
function whereLine(match: Match, located: Locate | null | undefined, t: Translate): string {
  if (located?.found) {
    if (located.kind === "pdf" && located.page !== undefined && located.page_count !== undefined) {
      const where = t("source.pageOf", { page: located.page, count: located.page_count });
      return located.ocr ? `${where} · OCR` : where;
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
 * passage of the source it matches (frame 2b). Below, a PDF shows its real page with the passage
 * marked; any other file, or a PDF that cannot be located, shows the snapshot text the answer saw
 * with the passage highlighted. Opened from a source card it shows the same for the whole snapshot.
 */
export default function SourcePanel({
  sources,
  messageId,
  multiProject = false,
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
  // A selection locates its matched window; a source opened as it is locates the whole snapshot.
  // The project's notes have no file: their saved text is all there is to show.
  const targetSource = match ? sources.find((s) => s.n === match.n) : source;
  const notesTarget = !!targetSource && isProjectNotes(targetSource);
  const target = notesTarget
    ? null
    : match
      ? { n: match.n, start: match.start, end: match.end }
      : !check && source
        ? { n: source.n, start: null, end: null }
        : null;
  // Undefined while loading, null when it failed. The selected answer text goes along, so the page
  // can mark just those words where the passage has them word for word.
  const located = useLocated(messageId, target, match ? answerFocus(check?.selection) : undefined);
  // Without the answer's sources there is no snapshot to offset into: show none.
  const snapshot = match ? (sources.find((s) => s.n === match.n)?.text ?? null) : null;

  const title = match ? match.file : (source?.file ?? null);
  const titled = match ? sources.find((s) => s.n === match.n) : source;
  const shownTitle = title ? (titled ? shownFile(titled, multiProject, t("source.projectNotes")) : fileName(title)) : null;
  const subtitle = match ? whereLine(match, located, t) : source ? sourceLabel(source) : "";
  const fileId = match ? match.file_id : (source?.file_id ?? null);
  // The real page, once located: a text file's snapshot is already its real text. While the next
  // passage of the same file is located, the page stays as it is, with its own marks.
  const fresh = pdfLocated(located);
  const [held, setHeld] = useState<{ fileId: number; located: Locate } | null>(null);
  if (fresh && fileId != null && held?.located !== fresh) setHeld({ fileId, located: fresh });
  const preview =
    fresh ??
    (located === undefined && target && fileId != null && held?.fileId === fileId ? held.located : null);
  // The zoom belongs to the file shown: the next passage of the same file keeps it, another file
  // starts at 100% (the panel width).
  const [zoomed, setZoomed] = useState({ fileId, zoom: PANEL_ZOOM_STEPS[0] });
  if (zoomed.fileId !== fileId) setZoomed({ fileId, zoom: PANEL_ZOOM_STEPS[0] });
  const zoom = zoomed.fileId === fileId ? zoomed.zoom : PANEL_ZOOM_STEPS[0];
  const zoomAt = PANEL_ZOOM_STEPS.indexOf(zoom);
  const zoomTo = (at: number) => setZoomed({ fileId, zoom: PANEL_ZOOM_STEPS[at] });
  const body = useRef<HTMLDivElement>(null);
  const panel = useRef<HTMLElement>(null);
  const compare = useRef<HTMLElement>(null);
  // Null: Compare is as tall as its text; once dragged, this many pixels, the Document text filling it.
  const [compareHeight, setCompareHeight] = useState<number | null>(null);
  useDragScroll(body);
  useWheelZoom(body, (by) => {
    if (preview) zoomTo(Math.min(PANEL_ZOOM_STEPS.length - 1, Math.max(0, zoomAt + by)));
  });
  // Words selected in Compare are marked on the page instead, for the passage they were selected at.
  const targetKey = match ? `${check?.id}:${match.n}:${match.start}:${match.end}` : "";
  const [focus, setFocus] = useState<{ key: string; text: string } | null>(null);
  const focusText = focus?.key === targetKey && match && preview ? focus.text : null;
  const focused = useLocated(messageId, focusText ? target : null, focusText ?? undefined);
  const focusRects = focusText ? focused?.focus_rects : undefined;
  // The words selected in Compare, else the answer's selection, else the whole passage.
  const marks = focusRects?.length
    ? focusRects
    : preview?.focus_rects?.length
      ? preview.focus_rects
      : (preview?.rects ?? []);

  function selectFocus(section: HTMLElement) {
    const selection = window.getSelection();
    if (!selection || !section.contains(selection.anchorNode) || !section.contains(selection.focusNode)) {
      return;
    }
    const text = selection.toString().trim();
    // A click without a selection goes back to the answer's selection.
    setFocus(text && text.length <= MAX_SELECTION ? { key: targetKey, text } : null);
  }

  const previewPage =
    preview?.page !== undefined && fileId !== null && fileId !== undefined && title ? (
      <PdfPage
        key={fileId}
        fit
        fileId={fileId}
        file={fileName(title)}
        page={preview.page}
        zoom={zoom}
        highlight={{ page: preview.page, rects: marks }}
      />
    ) : null;
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
    <aside className="source-panel" ref={panel} aria-label={t("chat.sourcePanel")}>
      <header>
        <FileText size={20} />
        <div className="source-title">
          <strong title={title ?? undefined}>{shownTitle ?? t("chat.sourcePanel")}</strong>
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
        <section
          ref={compare}
          className={compareHeight === null ? "compare" : "compare sized"}
          style={compareHeight === null ? undefined : { height: compareHeight }}
          aria-label={t("source.compare")}
          onMouseUp={(event) => selectFocus(event.currentTarget)}
          onKeyUp={(event) => selectFocus(event.currentTarget)}
        >
          <h3 className="compare-title">{t("source.compare")}</h3>
          <div className="compare-row">
            <span className="compare-pill">{t("source.answer")}</span>
            <p className="compare-text" title={preview ? t("source.selectHint") : undefined}>
              <MathText text={check.selection} />
            </p>
          </div>
          {match && (
            <>
              <div className="compare-row document">
                <span className="compare-pill">{t("source.document")}</span>
                <p className="compare-text" title={preview ? t("source.selectHint") : undefined}>
                  {match.text}
                </p>
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
      {check && previewPage && (
        <Splitter
          label={t("source.resizeCompare")}
          orientation="horizontal"
          size={compareHeight ?? compare.current?.offsetHeight ?? 0}
          min={COMPARE_MIN}
          // Leave room for the zoom buttons and some of the page.
          max={() => (panel.current?.clientHeight ?? 600) - 200}
          grow={1}
          onResize={setCompareHeight}
        />
      )}
      {previewPage && (
        <div className="source-zoom">
          <div className="viewer-control">
            <button
              type="button"
              className="icon-button"
              aria-label={t("viewer.zoomOut")}
              disabled={zoomAt <= 0}
              onClick={() => zoomTo(Math.max(0, zoomAt - 1))}
            >
              <Minus size={18} />
            </button>
            <span className="viewer-readout">{zoom}%</span>
            <button
              type="button"
              className="icon-button"
              aria-label={t("viewer.zoomIn")}
              disabled={zoomAt >= PANEL_ZOOM_STEPS.length - 1}
              onClick={() => zoomTo(Math.min(PANEL_ZOOM_STEPS.length - 1, zoomAt + 1))}
            >
              <Plus size={18} />
            </button>
          </div>
        </div>
      )}
      <div className="source-body" ref={body}>
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
                            {shownFile(s, multiProject, t("source.projectNotes"))}
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
        {match && (previewPage || snapshot !== null) && (
          <>
            {match.changed && (
              <div className="source-warning" role="status">
                <TriangleAlert size={16} />
                <span>{t("source.changed")}</span>
              </div>
            )}
            {focusRects?.length === 0 && (
              <p className="source-state" role="status">
                {t("source.focusNotFound")}
              </p>
            )}
            {previewPage ?? (
              <div className="paper">
                <Snapshot text={snapshot ?? ""} start={match.start} end={match.end} />
              </div>
            )}
          </>
        )}
        {!check &&
          source &&
          (previewPage ?? (
            <div className="paper">
              <TextWithParagraphs text={source.text} />
            </div>
          ))}
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

/** The located page of a PDF that can be previewed, else null. */
function pdfLocated(located: Locate | null | undefined): Locate | null {
  return located?.found && !located.changed && located.kind === "pdf" && located.page !== undefined
    ? located
    : null;
}

/**
 * Where the shown passage (null offsets: the whole snapshot) is in the current file: undefined
 * until known, null when it cannot be told. A result is only used for the passage it was asked
 * for, so a step to the next match never shows the page of the previous one for a frame.
 */
function useLocated(
  messageId: number | null,
  target: { n: number; start: number | null; end: number | null } | null,
  focus?: string,
): Locate | null | undefined {
  const [result, setResult] = useState<{ key: string; located: Locate | null } | null>(null);
  const n = target?.n;
  const start = target?.start;
  const end = target?.end;
  const key = `${messageId}:${n}:${start}:${end}:${focus}`;
  useEffect(() => {
    if (messageId === null || n === undefined) return;
    let current = true;
    locateSource(messageId, n, start == null || end == null ? undefined : { start, end }, focus)
      .then((located) => {
        if (current) setResult({ key, located });
      })
      .catch(() => {
        // The header keeps the saved label, and the snapshot is shown.
        if (current) setResult({ key, located: null });
      });
    return () => {
      current = false;
    };
  }, [messageId, n, start, end, focus, key]);
  return result?.key === key ? result.located : undefined;
}
