import { type ReactNode, useEffect, useRef } from "react";

type Props = {
  labelledBy: string;
  onClose: () => void;
  small?: boolean;
  /** Most of the window (the document viewer). */
  large?: boolean;
  children: ReactNode;
};

/** A centered dialog over a dimmed backdrop. Esc (wherever focus is) or a backdrop click closes it. */
export default function Modal({ labelledBy, onClose, small, large, children }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const close = useRef(onClose);
  useEffect(() => {
    close.current = onClose;
  });

  useEffect(() => {
    const previous = document.activeElement;
    const first =
      box.current?.querySelector<HTMLElement>("[data-autofocus]") ??
      box.current?.querySelector<HTMLElement>("input, button") ??
      box.current;
    first?.focus();
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        close.current();
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      if (previous instanceof HTMLElement) previous.focus();
    };
  }, []);

  return (
    <div
      className="overlay"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) close.current();
      }}
    >
      <div
        ref={box}
        className={small ? "dialog small" : large ? "dialog large" : "dialog"}
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledBy}
        tabIndex={-1}
      >
        {children}
      </div>
    </div>
  );
}
