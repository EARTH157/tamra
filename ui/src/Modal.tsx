import { type KeyboardEvent, type ReactNode, useEffect, useRef } from "react";

type Props = {
  labelledBy: string;
  onClose: () => void;
  small?: boolean;
  children: ReactNode;
};

/** A centered dialog over a dimmed backdrop. Esc or a click on the backdrop closes it. */
export default function Modal({ labelledBy, onClose, small, children }: Props) {
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const previous = document.activeElement;
    const first =
      box.current?.querySelector<HTMLElement>("[data-autofocus]") ??
      box.current?.querySelector<HTMLElement>("input, button");
    first?.focus();
    return () => {
      if (previous instanceof HTMLElement) previous.focus();
    };
  }, []);

  function onKeyDown(event: KeyboardEvent) {
    if (event.key === "Escape") {
      event.stopPropagation();
      onClose();
    }
  }

  return (
    <div
      className="overlay"
      onKeyDown={onKeyDown}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={box}
        className={small ? "dialog small" : "dialog"}
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledBy}
      >
        {children}
      </div>
    </div>
  );
}
