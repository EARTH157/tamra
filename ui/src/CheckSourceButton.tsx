import { SearchCheck } from "lucide-react";
import { type RefObject, useEffect, useRef, useState } from "react";
import { useT } from "./i18n";
import { type Selected, selectionInside } from "./selection";

type Props = {
  /** The element that holds the answer's rendered text. */
  rootRef: RefObject<HTMLElement | null>;
  /** The message content the text was rendered from. */
  content: string;
  onCheck: (selected: Selected) => void;
};

type Shown = Selected & { left: number; top: number };

/**
 * A small "Check source" button near the end of a selection inside one answer's text. It appears
 * when a selection is made (not while it is being dragged), and goes away when the selection is
 * collapsed or leaves the text, on scroll, on Esc, and on a click elsewhere.
 */
export default function CheckSourceButton({ rootRef, content, onCheck }: Props) {
  const t = useT();
  const [shown, setShown] = useState<Shown | null>(null);
  const button = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const root = rootRef.current;
    if (!root) return;
    let dragging = false;
    function evaluate() {
      const selected = root ? selectionInside(root, content) : null;
      const range = selected ? window.getSelection()?.getRangeAt(0) : null;
      if (!selected || !range) {
        setShown(null);
        return;
      }
      const rects = range.getClientRects?.();
      const rect =
        (rects && rects.length > 0 ? rects[rects.length - 1] : null) ??
        range.getBoundingClientRect?.() ??
        root?.getBoundingClientRect();
      const width = 150; // keep the button inside the window
      setShown({
        ...selected,
        left: Math.max(8, Math.min(rect?.right ?? 0, window.innerWidth - width)),
        top: (rect?.bottom ?? 0) + 6,
      });
    }
    const hide = () => setShown(null);
    function onMouseDown(event: Event) {
      if (event.target instanceof Node && button.current?.contains(event.target)) return;
      dragging = true;
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
      if (event.key === "Escape") hide();
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

  if (!shown) return null;
  return (
    <button
      ref={button}
      type="button"
      className="check-source"
      style={{ left: shown.left, top: shown.top }}
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
