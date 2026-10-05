import { type ReactNode, useEffect, useRef } from "react";

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** Keep Tab and Shift+Tab inside the dialog: past the last control it goes to the first, and back. */
function trapTab(event: KeyboardEvent, box: HTMLElement) {
  const controls = [...box.querySelectorAll<HTMLElement>(FOCUSABLE)];
  if (controls.length === 0) {
    event.preventDefault();
    box.focus();
    return;
  }
  const first = controls[0];
  const last = controls[controls.length - 1];
  const active = document.activeElement;
  if (!(active instanceof Node) || !box.contains(active) || active === box) {
    event.preventDefault();
    (event.shiftKey ? last : first).focus();
  } else if (event.shiftKey && active === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && active === last) {
    event.preventDefault();
    first.focus();
  }
}

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
      } else if (event.key === "Tab" && box.current) {
        trapTab(event, box.current);
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
