import type { KeyboardEvent, PointerEvent } from "react";

/** How far one arrow key press moves a splitter, in CSS pixels. */
export const SPLITTER_STEP = 16;

type Props = {
  label: string;
  /** "vertical": a bar between columns that sets a width; "horizontal": between rows, a height. */
  orientation: "vertical" | "horizontal";
  /** The size it sets now, in CSS pixels. */
  size: number;
  min: number;
  /** Read at each move, so it follows the window. */
  max: () => number;
  /** 1 when moving right (or down) makes the size larger; -1 when it makes it smaller. */
  grow: 1 | -1;
  onResize: (size: number) => void;
};

/**
 * A bar to drag between two panes, which sets the size of one of them. It is a focusable separator,
 * so the arrow keys move it too, and Home and End take it to its smallest and largest size.
 */
export default function Splitter({ label, orientation, size, min, max, grow, onResize }: Props) {
  const vertical = orientation === "vertical";
  const clamp = (value: number) => Math.round(Math.min(Math.max(min, max()), Math.max(min, value)));

  function onPointerDown(event: PointerEvent<HTMLDivElement>) {
    if (event.button !== 0) return;
    event.preventDefault(); // no text selection while dragging
    const bar = event.currentTarget;
    const from = vertical ? event.clientX : event.clientY;
    const start = size;
    bar.setPointerCapture?.(event.pointerId);
    bar.classList.add("dragging");
    const move = (e: globalThis.PointerEvent) =>
      onResize(clamp(start + grow * ((vertical ? e.clientX : e.clientY) - from)));
    const end = () => {
      bar.classList.remove("dragging");
      bar.removeEventListener("pointermove", move);
      bar.removeEventListener("pointerup", end);
      bar.removeEventListener("pointercancel", end);
    };
    bar.addEventListener("pointermove", move);
    bar.addEventListener("pointerup", end);
    bar.addEventListener("pointercancel", end);
  }

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const [less, more] = vertical ? ["ArrowLeft", "ArrowRight"] : ["ArrowUp", "ArrowDown"];
    let next: number;
    if (event.key === less) next = size - grow * SPLITTER_STEP;
    else if (event.key === more) next = size + grow * SPLITTER_STEP;
    else if (event.key === "Home") next = min;
    else if (event.key === "End") next = max();
    else return;
    event.preventDefault();
    onResize(clamp(next));
  }

  return (
    <div
      className={`splitter ${orientation}`}
      role="separator"
      aria-label={label}
      aria-orientation={orientation}
      aria-valuenow={size}
      aria-valuemin={min}
      aria-valuemax={Math.max(min, Math.round(max()))}
      tabIndex={0}
      onPointerDown={onPointerDown}
      onKeyDown={onKeyDown}
    />
  );
}
