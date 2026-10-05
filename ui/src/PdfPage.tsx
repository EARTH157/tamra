import { TriangleAlert } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { fetchBlob } from "./api";
import { viewerErrorText } from "./apiErrors";
import { useT } from "./i18n";

/** The most page images asked for at once: each one holds a worker of the core while it renders. */
const MAX_IN_FLIGHT = 2;
const MIN_SCALE = 0.5;
const MAX_SCALE = 3;

let active = 0;
const waiting: Array<() => void> = [];

/** Wait for one of the request slots; rejects, without taking one, if `signal` aborts first. */
function acquire(signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException("Aborted", "AbortError"));
      return;
    }
    if (active < MAX_IN_FLIGHT) {
      active++;
      resolve();
      return;
    }
    const start = () => {
      signal.removeEventListener("abort", onAbort);
      active++;
      resolve();
    };
    const onAbort = () => {
      const at = waiting.indexOf(start);
      if (at >= 0) waiting.splice(at, 1);
      reject(new DOMException("Aborted", "AbortError"));
    };
    waiting.push(start);
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

function release() {
  active--;
  waiting.shift()?.();
}

/** A page image, at most MAX_IN_FLIGHT at a time; a request abandoned while it waits is never sent. */
async function fetchPage(path: string, signal: AbortSignal): Promise<Blob> {
  await acquire(signal);
  try {
    return await fetchBlob(path, signal);
  } finally {
    release();
  }
}

/** The render scale for a zoom (percent) on a screen with the given pixel ratio: sharp, but capped. */
export function pageScale(zoom: number, pixelRatio: number): number {
  const scale = (zoom / 100) * (pixelRatio || 1);
  return Math.round(Math.min(MAX_SCALE, Math.max(MIN_SCALE, scale)) * 100) / 100;
}

const percent = (fraction: number) => Number((fraction * 100).toFixed(3));

/** A loaded image: the render scale and the zoom it was asked for, which together give its CSS size. */
type Shown = { url: string; page: number; scale: number; zoom: number };

type Props = {
  fileId: number;
  /** The file name, for the image's alt text. */
  file: string;
  /** 1-based. */
  page: number;
  /** Percent. */
  zoom: number;
  /** The passage to mark: boxes as fractions of the page, drawn only while that page is shown. */
  highlight: { page: number; rects: number[][] } | null;
};

/**
 * One page of a PDF as the image the core renders, with the matched passage marked by boxes over
 * it. The boxes are fractions of the page, so they follow the zoom. While a new image loads the
 * previous one stays; every object URL is revoked once replaced and when the page goes away.
 */
export default function PdfPage({ fileId, file, page, zoom, highlight }: Props) {
  const t = useT();
  const scale = pageScale(zoom, window.devicePixelRatio);
  const key = `${fileId}:${page}:${scale}`;
  const [shown, setShown] = useState<Shown | null>(null);
  const [failed, setFailed] = useState<{ key: string; error: Error } | null>(null);
  // The page's CSS width at 100%, known once an image has loaded: its pixels over its scale. The page
  // is shown at that times the zoom of the image on screen, so zooming changes the size, and until the
  // next image arrives the size stays the one of the image shown.
  const [fullWidth, setFullWidth] = useState<number | null>(null);
  const zoomRef = useRef(zoom);
  zoomRef.current = zoom;
  // The image that has loaded and been sized; the passage is scrolled to only then, as before that the
  // page has no height.
  const [loadedUrl, setLoadedUrl] = useState<string | null>(null);
  const urlRef = useRef<string | null>(null);
  const firstBox = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    fetchPage(`/api/files/${fileId}/pages/${page}?scale=${scale}`, controller.signal)
      .then((blob) => {
        if (controller.signal.aborted) return;
        const url = URL.createObjectURL(blob);
        const previous = urlRef.current;
        urlRef.current = url;
        setShown({ url, page, scale, zoom: zoomRef.current });
        if (previous) URL.revokeObjectURL(previous);
      })
      .catch((error: Error) => {
        if (!controller.signal.aborted) setFailed({ key, error });
      });
    return () => controller.abort();
  }, [fileId, page, scale, key]);

  // A zoom that renders at the same scale (the cap) needs no new image, only a new size.
  useEffect(() => {
    setShown((s) => (s && s.page === page && s.scale === scale && s.zoom !== zoom ? { ...s, zoom } : s));
  }, [zoom, page, scale]);

  useEffect(
    () => () => {
      if (urlRef.current) URL.revokeObjectURL(urlRef.current);
      urlRef.current = null;
    },
    [],
  );

  const marks = shown && highlight && highlight.page === shown.page ? highlight.rects : [];
  const markKey = shown ? `${shown.url}:${JSON.stringify(marks)}` : "";
  const width = fullWidth !== null && shown ? Math.round(fullWidth * shown.zoom) / 100 : null;
  const sized = !!shown && loadedUrl === shown.url;
  // Scroll to the first box when the marks appear on a loaded image (a new image or a new passage).
  useEffect(() => {
    if (sized && marks.length > 0) firstBox.current?.scrollIntoView?.({ block: "center" });
  }, [markKey, sized]);

  const current = shown?.page === page && shown.scale === scale;
  const error = failed?.key === key ? failed.error : null;
  const unmarked = !!shown && !!highlight && highlight.page === shown.page && highlight.rects.length === 0;

  return (
    <>
      {error && (
        <div className="chat-error" role="alert">
          <span>{viewerErrorText(error, t)}</span>
        </div>
      )}
      {unmarked && (
        <div className="source-warning" role="status">
          <TriangleAlert size={16} />
          <span>{t("viewer.noMarks")}</span>
        </div>
      )}
      {!current && !error && (
        <p className="source-state" role="status">
          {t("viewer.pageLoading")}
        </p>
      )}
      {shown && (
        <div
          className="pdf-page"
          style={{ width: width ?? undefined, visibility: width === null ? "hidden" : "visible" }}
        >
          <img
            src={shown.url}
            alt={t("viewer.pageAlt", { page: shown.page, file })}
            onLoad={(event) => {
              const natural = event.currentTarget.naturalWidth;
              if (natural > 0) setFullWidth(natural / shown.scale);
              setLoadedUrl(shown.url);
            }}
          />
          {marks.map(([x0, y0, x1, y1], i) => (
            <div
              key={i}
              ref={i === 0 ? firstBox : undefined}
              className="pdf-hit"
              aria-hidden="true"
              style={{
                left: `${percent(x0)}%`,
                top: `${percent(y0)}%`,
                width: `${percent(x1 - x0)}%`,
                height: `${percent(y1 - y0)}%`,
              }}
            />
          ))}
        </div>
      )}
    </>
  );
}
