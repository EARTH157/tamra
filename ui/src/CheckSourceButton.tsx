import { SearchCheck } from "lucide-react";
import { type RefObject, useEffect, useLayoutEffect, useRef, useState } from "react";
import { useT } from "./i18n";
import { type Selected, selectionInside } from "./selection";

type Props = {
  /** The element that holds the answer's rendered text. */
  rootRef: RefObject<HTMLElement | null>;
  /** The message content the text was rendered from. */
  content: string;
  onCheck: (selected: Selected) => void;
};

/** Where the selection ends on screen (viewport pixels): the button goes under it, or above. */
type Anchor = { right: number; top: number; bottom: number };
type Shown = Selected & { anchor: Anchor };

const GAP = 6;
const MARGIN = 8;

/**
 * A small "Check source" button near the end of a selection inside one answer's text. It appears
 * when a selection is made (not while it is being dragged), and goes away when the selection is
 * collapsed or leaves the text, on scroll, on Esc, and on a click elsewhere. After Esc it stays
 * away until the selection changes or the mouse is pressed again: the key's own keyup must not
 * bring it back.
 */
export default function CheckSourceButton({ rootRef, content, onCheck }: Props) {
  const t = useT();
  const [shown, setShown] = useState<Shown | null>(null);
  const button = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const root = rootRef.current;
    if (!root) return;
    let dragging = false;
    // The range that Esc dismissed the button for; the same range does not show it again.
    let dismissed: Range | null = null;
    function evaluate() {
      const selected = root ? selectionInside(root, content) : null;
      const range = selected ? window.getSelection()?.getRangeAt(0) : null;
      if (!selected || !range) {
        dismissed = null;
        setShown(null);
        return;
      }
      if (
        dismissed &&
        dismissed.startContainer === range.startContainer &&
        dismissed.startOffset === range.startOffset &&
        dismissed.endContainer === range.endContainer &&
        dismissed.endOffset === range.endOffset
      ) {
        return;
      }
      dismissed = null;
      const rects = range.getClientRects?.();
      const rect =
        (rects && rects.length > 0 ? rects[rects.length - 1] : null) ??
        range.getBoundingClientRect?.() ??
        root?.getBoundingClientRect();
      setShown({
        ...selected,
        anchor: { right: rect?.right ?? 0, top: rect?.top ?? 0, bottom: rect?.bottom ?? 0 },
      });
    }
    const hide = () => setShown(null);
    function onMouseDown(event: Event) {
      if (event.target instanceof Node && button.current?.contains(event.target)) return;
      dragging = true;
      dismissed = null;
      hide();
    }
    function onMouseUp() {
      dragging = false;
      evaluate();
    }
    function onSelectionChange() {
      if (!dragging) evaluate();
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      const selection = window.getSelection();
      if (selection && selection.rangeCount > 0) dismissed = selection.getRangeAt(0).cloneRange();
      hide();
    }
    document.addEventListener("mousedown", onMouseDown);
    document.addEventListener("mouseup", onMouseUp);
    document.addEventListener("keyup", onSelectionChange);
    document.addEventListener("selectionchange", onSelectionChange);
    document.addEventListener("keydown", onKeyDown);
    window.addEventListener("scroll", hide, true);
    return () => {
      document.removeEventListener("mousedown", onMouseDown);
      document.removeEventListener("mouseup", onMouseUp);
      document.removeEventListener("keyup", onSelectionChange);
      document.removeEventListener("selectionchange", onSelectionChange);
      document.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("scroll", hide, true);
    };
  }, [rootRef, content]);

  // Place the button once its own size is known: inside the window, and above the selection when
  // there is no room below it.
  useLayoutEffect(() => {
    const el = button.current;
    if (!shown || !el) return;
    const { right, top, bottom } = shown.anchor;
    const width = el.offsetWidth;
    const height = el.offsetHeight;
    el.style.left = `${Math.max(MARGIN, Math.min(right, window.innerWidth - width - MARGIN))}px`;
    const below = bottom + GAP;
    el.style.top = `${below + height > window.innerHeight - MARGIN ? Math.max(MARGIN, top - GAP - height) : below}px`;
  }, [shown]);

  if (!shown) return null;
  return (
    <button
      ref={button}
      type="button"
      className="check-source"
      // Keep the selection and the focus where they are while the button is pressed.
      onMouseDown={(event) => event.preventDefault()}
      onClick={() => {
        const { text, start, end } = shown;
        setShown(null);
        window.getSelection()?.removeAllRanges();
        onCheck({ text, start, end });
      }}
    >
      <SearchCheck size={15} />
      {t("chat.checkSource")}
    </button>
  );
}
