import { ChevronLeft, ChevronRight, FileText, Minus, Plus, TriangleAlert, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { ApiError, api, locateSource } from "./api";
import { errorText } from "./apiErrors";
import Modal from "./Modal";
import { type TranslationKey, useT } from "./i18n";
import { cpToUtf16 } from "./offsets";
import PdfPage from "./PdfPage";
import { fileName, folderOf } from "./SourcePanel";
import type { Locate, Source, TextDoc, ViewerRequest } from "./types";

/** The zoom steps of a PDF page, in percent. */
export const ZOOM_STEPS = [50, 75, 100, 125, 150, 200];
const DEFAULT_ZOOM = 100;

type Translate = (key: TranslationKey, params?: Record<string, string | number>) => string;

/** Which passage of which source the viewer shows; null offsets mean the whole snapshot. */
type Target = { n: number; fileId: number; file: string; start: number | null; end: number | null };

type Located = { key: string; located: Locate | null; error: string | null };

const targetKey = (target: Target) => `${target.n}:${target.start}:${target.end}`;

/** Where the passage is in the current file: its page, its lines or its paragraphs. */
function placeLabel(located: Locate | null, t: Translate): string {
  if (!located?.found) return "";
  const { start, end } = located;
  if (located.kind === "pdf") {
    return located.page === undefined ? "" : t("viewer.page", { page: located.page });
  }
  if (start === undefined || end === undefined) return "";
  const last = end - 1;
  if (located.kind === "text") {
    return start === last ? t("source.line", { n: start }) : t("source.lines", { from: start, to: last });
  }
  // Paragraphs are numbered from 0 in the text view; people count from 1.
  return start === last
    ? t("viewer.paragraph", { n: start + 1 })
    : t("viewer.paragraphs", { from: start + 1, to: last + 1 });
}

type Props = {
  request: ViewerRequest;
  /** The answer's sources: the snapshot text of the passage, and the other sources to switch to. */
  sources: Source[];
  onClose: () => void;
};

/**
 * The document viewer (the "Dialog · Open file" frame): the file as it is now, with the passage an
 * answer used marked. The left pane has the answer text and the passage side by side and the
 * answer's other sources; the right pane has the page image of a PDF, or the text of a DOCX, TXT
 * or MD file with line or paragraph numbers.
 */
export default function DocumentViewer({ request, sources, onClose }: Props) {
  const t = useT();
  const [initial] = useState<Target>(() => ({
    n: request.n,
    fileId: request.fileId,
    file: request.file,
    start: request.start,
    end: request.end,
  }));
  const [target, setTarget] = useState<Target>(initial);
  const [result, setResult] = useState<Located | null>(null);
  const [pageState, setPageState] = useState<{ key: string; page: number } | null>(null);
  const [zoom, setZoom] = useState(DEFAULT_ZOOM);
  // False once the core says that opening a file is not possible here (the dev server).
  const [canOpen, setCanOpen] = useState(true);
  const [openError, setOpenError] = useState<string | null>(null);

  const key = targetKey(target);
  useEffect(() => {
    let current = true;
    const range =
      target.start === null || target.end === null ? undefined : { start: target.start, end: target.end };
    locateSource(request.messageId, target.n, range)
      .then((located) => {
        if (current) setResult({ key, located, error: null });
      })
      .catch((error: Error) => {
        if (current) setResult({ key, located: null, error: errorText(error, t) });
      });
    return () => {
      current = false;
    };
  }, [request.messageId, target.n, target.start, target.end, key, t]);

  const state = result?.key === key ? result : null;
  const located = state?.located ?? null;
  const isPdf = located?.kind === "pdf";
  const pageCount = located?.page_count ?? 1;
  const locatedPage = located?.found ? (located.page ?? 1) : 1;
  const page = Math.min(pageCount, Math.max(1, pageState?.key === key ? pageState.page : locatedPage));

  const snapshot = sources.find((source) => source.n === target.n)?.text ?? null;
  const passage =
    snapshot === null
      ? null
      : target.start === null || target.end === null
        ? snapshot
        : snapshot.slice(cpToUtf16(snapshot, target.start), cpToUtf16(snapshot, target.end));
  const others = sources.filter((source) => source.n !== target.n && source.file_id != null);
  const place = placeLabel(located, t);
  const showSelection = !!request.selection && target === initial;

  function switchTo(source: Source) {
    if (source.file_id == null) return;
    setOpenError(null);
    setTarget({ n: source.n, fileId: source.file_id, file: source.file, start: null, end: null });
  }

  async function openDefault() {
    setOpenError(null);
    try {
      await api("POST", `/api/files/${target.fileId}/open`);
    } catch (error) {
      if (error instanceof ApiError && error.status === 501) setCanOpen(false);
      else setOpenError(errorText(error as Error, t));
    }
  }

  const zoomAt = ZOOM_STEPS.indexOf(zoom);
  const folder = folderOf(target.file);

  return (
    <Modal labelledBy="viewer-title" onClose={onClose} large>
      <header className="viewer-header">
        <FileText size={22} className="viewer-icon" />
        <div className="viewer-title">
          <h2 id="viewer-title" title={target.file}>
            {fileName(target.file)}
          </h2>
          {folder && <span title={folder}>{folder}</span>}
        </div>
        {isPdf && (
          <>
            <div className="viewer-control">
              <button
                type="button"
                className="icon-button"
                aria-label={t("viewer.previousPage")}
                disabled={page <= 1}
                onClick={() => setPageState({ key, page: page - 1 })}
              >
                <ChevronLeft size={18} />
              </button>
              <span className="viewer-readout">{t("viewer.pageOf", { page, count: pageCount })}</span>
              <button
                type="button"
                className="icon-button"
                aria-label={t("viewer.nextPage")}
                disabled={page >= pageCount}
                onClick={() => setPageState({ key, page: page + 1 })}
              >
                <ChevronRight size={18} />
              </button>
            </div>
            <div className="viewer-control">
              <button
                type="button"
                className="icon-button"
                aria-label={t("viewer.zoomOut")}
                disabled={zoomAt <= 0}
                onClick={() => setZoom(ZOOM_STEPS[Math.max(0, zoomAt - 1)])}
              >
                <Minus size={18} />
              </button>
              <span className="viewer-readout">{zoom}%</span>
              <button
                type="button"
                className="icon-button"
                aria-label={t("viewer.zoomIn")}
                disabled={zoomAt >= ZOOM_STEPS.length - 1}
                onClick={() => setZoom(ZOOM_STEPS[Math.min(ZOOM_STEPS.length - 1, zoomAt + 1)])}
              >
                <Plus size={18} />
              </button>
            </div>
          </>
        )}
        {canOpen ? (
          <button type="button" className="btn" onClick={() => void openDefault()}>
            {t("viewer.openDefault")}
          </button>
        ) : (
          <span className="viewer-unavailable">{t("settings.openFolderUnavailable")}</span>
        )}
        <button
          type="button"
          className="icon-button"
          aria-label={t("viewer.close")}
          data-autofocus
          onClick={onClose}
        >
          <X size={20} />
        </button>
      </header>
      <div className="viewer-body">
        <div className="viewer-left">
          {showSelection && (
            <section className="viewer-section">
              <h3 className="viewer-heading">{t("viewer.selected")}</h3>
              <p className="viewer-selected">{request.selection}</p>
            </section>
          )}
          {located?.found && passage !== null && (
            <section className="viewer-section">
              <h3 className="viewer-heading">{t("viewer.found")}</h3>
              <div className="viewer-found">
                <p>{passage}</p>
                {place && <span className="viewer-where">{place}</span>}
              </div>
            </section>
          )}
          {others.length > 0 && (
            <section className="viewer-section viewer-others">
              <h3 className="viewer-heading">{t("viewer.others")}</h3>
              <ul className="source-cards">
                {others.map((source) => (
                  <li key={source.n}>
                    <button type="button" className="source-card" onClick={() => switchTo(source)}>
                      <span className="source-num">{source.n}</span>
                      <span className="source-meta">
                        <span className="source-file" title={source.file}>
                          {fileName(source.file)}
                        </span>
                        {source.label && <span className="source-label">{source.label}</span>}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          )}
          <p className="viewer-note">{t("viewer.note")}</p>
        </div>
        <div className="viewer-stage">
          {openError && (
            <div className="chat-error" role="alert">
              <span>
                {t("viewer.openFailed")} {openError}
              </span>
            </div>
          )}
          {located?.changed && (
            <div className="source-warning" role="status">
              <TriangleAlert size={16} />
              <span>{t("source.changed")}</span>
            </div>
          )}
          {located && !located.found && (
            <div className="source-warning" role="status">
              <TriangleAlert size={16} />
              <span>{t("viewer.notFound")}</span>
            </div>
          )}
          {!state && (
            <p className="source-state" role="status">
              {t("viewer.loading")}
            </p>
          )}
          {state?.error && (
            <div className="chat-error" role="alert">
              <span>
                {t("viewer.failed")} {state.error}
              </span>
            </div>
          )}
          {located && isPdf && (
            <PdfPage
              fileId={target.fileId}
              file={fileName(target.file)}
              page={page}
              zoom={zoom}
              highlight={located.found && located.page ? { page: located.page, rects: located.rects ?? [] } : null}
            />
          )}
          {located && !isPdf && (
            <TextView
              fileId={target.fileId}
              highlight={
                located.found && located.start !== undefined && located.end !== undefined
                  ? { start: located.start, end: located.end }
                  : null
              }
            />
          )}
        </div>
      </div>
    </Modal>
  );
}

type Row = { number: number; text: string; heading: boolean; hit: boolean };

/** The rows of a text file (numbered by line) or a DOCX (by paragraph), those in the range marked. */
function rowsOf(doc: TextDoc, highlight: { start: number; end: number } | null): Row[] {
  const inRange = (value: number) => !!highlight && value >= highlight.start && value < highlight.end;
  if (doc.kind === "text") {
    // Lines are numbered from 1, and the range is in those numbers.
    return doc.lines.map((text, i) => ({ number: i + 1, text, heading: false, hit: inRange(i + 1) }));
  }
  // Paragraphs are indexed from 0 in the range, and numbered from 1 for people.
  return doc.paragraphs.map((p) => ({
    number: p.index + 1,
    text: p.text,
    heading: p.heading,
    hit: inRange(p.index),
  }));
}

/** A DOCX, TXT or MD file as extracted text on a "paper", the located range marked and in view. */
function TextView({
  fileId,
  highlight,
}: {
  fileId: number;
  highlight: { start: number; end: number } | null;
}) {
  const t = useT();
  const [loaded, setLoaded] = useState<{ fileId: number; doc: TextDoc | null; error: string | null } | null>(
    null,
  );
  const firstHit = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let current = true;
    api<TextDoc>("GET", `/api/files/${fileId}/text`)
      .then((doc) => {
        if (current) setLoaded({ fileId, doc, error: null });
      })
      .catch((error: Error) => {
        if (current) setLoaded({ fileId, doc: null, error: errorText(error, t) });
      });
    return () => {
      current = false;
    };
  }, [fileId, t]);

  const now = loaded?.fileId === fileId ? loaded : null;
  const doc = now?.doc ?? null;
  const start = highlight?.start;
  const end = highlight?.end;
  // Bring the marked rows into view when the text arrives or the range changes.
  useEffect(() => {
    if (doc && start !== undefined) firstHit.current?.scrollIntoView?.({ block: "center" });
  }, [doc, start, end]);

  if (!now) {
    return (
      <p className="source-state" role="status">
        {t("viewer.loading")}
      </p>
    );
  }
  if (!doc) {
    return (
      <div className="chat-error" role="alert">
        <span>
          {t("viewer.failed")} {now.error}
        </span>
      </div>
    );
  }
  const rows = rowsOf(doc, highlight);
  const firstHitNumber = rows.find((row) => row.hit)?.number;
  return (
    <>
      <div className="viewer-paper">
        {rows.map((row) => (
          <div
            key={row.number}
            ref={row.number === firstHitNumber ? firstHit : undefined}
            className={`viewer-line${row.hit ? " hit" : ""}${row.heading ? " heading" : ""}`}
          >
            <span className="no" aria-hidden="true">
              {row.number}
            </span>
            <span className="line-text">{row.text}</span>
          </div>
        ))}
      </div>
      {doc.truncated && <p className="source-state">{t("viewer.truncated")}</p>}
    </>
  );
}
