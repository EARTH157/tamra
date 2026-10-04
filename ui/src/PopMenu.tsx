import { type KeyboardEvent, type ReactNode, useEffect, useLayoutEffect, useRef, useState } from "react";

const GAP = 6; // between the chip and the menu
const MARGIN = 8; // the menu keeps this far from the window edges

type Props = {
  /** The chip that opened the menu: the menu sits above it, with their left edges together. */
  anchor: HTMLElement;
  label: string;
  className?: string;
  /** `refocus` is true when the menu closed from the keyboard, so focus goes back to the chip. */
  onClose: (refocus: boolean) => void;
  children: ReactNode;
};

/**
 * A popover menu for the composer chips. It opens upward, closes on Esc (wherever focus is), Tab
 * or a click outside, and moves between its enabled items with the arrow keys. Items are
 * `role="menuitem*"` buttons.
 */
export default function PopMenu({ anchor, label, className, onClose, children }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const [place, setPlace] = useState(() => placement(anchor, 0));
  const close = useRef(onClose);
  useEffect(() => {
    close.current = onClose;
  });

  useLayoutEffect(() => {
    setPlace(placement(anchor, box.current?.getBoundingClientRect().width ?? 0));
  }, [anchor]);

  useEffect(() => {
    const items = enabledItems(box.current);
    (items.find((item) => item.getAttribute("aria-checked") === "true") ?? items[0])?.focus();
    function outside(event: MouseEvent) {
      const target = event.target as Node;
      if (box.current?.contains(target) || anchor.contains(target)) return; // the chip toggles it
      close.current(false);
    }
    // Esc closes the menu wherever focus is, e.g. after a click on the menu's padding.
    function escape(event: globalThis.KeyboardEvent) {
      if (event.key !== "Escape") return;
      event.preventDefault();
      close.current(true);
    }
    document.addEventListener("mousedown", outside);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("mousedown", outside);
      document.removeEventListener("keydown", escape);
    };
  }, [anchor]);

  function onKeyDown(event: KeyboardEvent) {
    if (event.key === "Tab") {
      event.preventDefault();
      onClose(true);
    } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const items = enabledItems(box.current);
      const at = items.indexOf(document.activeElement as HTMLElement);
      const step = event.key === "ArrowDown" ? 1 : -1;
      items[(at + step + items.length) % items.length]?.focus();
    }
  }

  return (
    <div
      ref={box}
      className={className ? `menu pop-menu ${className}` : "menu pop-menu"}
      role="menu"
      aria-label={label}
      style={place}
      onKeyDown={onKeyDown}
    >
      {children}
    </div>
  );
}

function enabledItems(root: HTMLElement | null): HTMLElement[] {
  return [
    ...(root?.querySelectorAll<HTMLElement>('[role^="menuitem"]:not(:disabled)') ?? []),
  ];
}

/** Bottom-anchored above the chip and kept inside the window sideways. */
function placement(anchor: HTMLElement, width: number): { bottom: number; left: number } {
  const rect = anchor.getBoundingClientRect();
  const maxLeft = window.innerWidth - width - MARGIN;
  return {
    bottom: window.innerHeight - rect.top + GAP,
    left: Math.max(MARGIN, Math.min(rect.left, maxLeft)),
  };
}
