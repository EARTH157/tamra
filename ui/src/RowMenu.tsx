import { ChevronRight } from "lucide-react";
import {
  type KeyboardEvent,
  type ReactNode,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";

const GAP = 3; // between the ⋯ button and the menu
const MARGIN = 8; // the menu keeps this far from the window edges

type Props = {
  /** The ⋯ button's edges (viewport pixels). */
  above: number;
  below: number;
  left: number;
  label: string;
  /** A small caption above the items (the project's name). */
  heading?: string;
  /** `refocus` is true when the menu closed from the keyboard, so focus goes back to the ⋯ button. */
  onClose: (refocus: boolean) => void;
  children: ReactNode;
};

/** The enabled items of one menu level (not those of a submenu inside it). */
function levelItems(menu: HTMLElement | null): HTMLElement[] {
  return [...(menu?.querySelectorAll<HTMLElement>('[role="menuitem"]:not(:disabled)') ?? [])].filter(
    (item) => item.closest('[role="menu"]') === menu,
  );
}

function step(menu: HTMLElement | null, by: number) {
  const items = levelItems(menu);
  const at = items.indexOf(document.activeElement as HTMLElement);
  items[(at + by + items.length) % items.length]?.focus();
}

/**
 * A row's ⋯ menu, next to the button: below it, or above it when there is no room, and kept inside
 * the window. Esc or Tab closes it and returns to the button; a click outside closes it; the arrow
 * keys move between its items. Items are `role="menuitem"` buttons; `SubMenu` adds a flyout.
 */
export default function RowMenu({ above, below, left, label, heading, onClose, children }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const [place, setPlace] = useState({ top: below + GAP, left });

  useLayoutEffect(() => {
    const rect = box.current?.getBoundingClientRect();
    if (!rect) return;
    const maxTop = window.innerHeight - rect.height - MARGIN;
    const maxLeft = window.innerWidth - rect.width - MARGIN;
    let top = below + GAP;
    if (top > maxTop) top = above - GAP - rect.height;
    setPlace({
      top: Math.max(MARGIN, Math.min(top, maxTop)),
      left: Math.max(MARGIN, Math.min(left, maxLeft)),
    });
  }, [above, below, left]);

  const close = useRef(onClose);
  useEffect(() => {
    close.current = onClose;
  });

  useEffect(() => {
    levelItems(box.current)[0]?.focus();
    function outside(event: MouseEvent) {
      const target = event.target as Node;
      if (box.current?.contains(target)) return;
      if (target instanceof Element && target.closest(".row-more")) return; // the toggle
      close.current(false);
    }
    // Esc closes the menu wherever focus is, e.g. after a click on the menu's padding. A key the
    // menu already handled (it called preventDefault) is not handled twice.
    function escape(event: globalThis.KeyboardEvent) {
      if (event.key !== "Escape" || event.defaultPrevented) return;
      event.preventDefault();
      close.current(true);
    }
    document.addEventListener("mousedown", outside);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("mousedown", outside);
      document.removeEventListener("keydown", escape);
    };
  }, []);

  function onKeyDown(event: KeyboardEvent) {
    if (event.key === "Escape" || event.key === "Tab") {
      event.preventDefault();
      onClose(true);
    } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      step(box.current, event.key === "ArrowDown" ? 1 : -1);
    }
  }

  return (
    <div ref={box} className="menu" role="menu" aria-label={label} style={place} onKeyDown={onKeyDown}>
      {heading && (
        <div className="menu-heading" aria-hidden="true">
          {heading}
        </div>
      )}
      {children}
    </div>
  );
}

type SubMenuProps = {
  label: string;
  icon: ReactNode;
  disabled?: boolean;
  children: ReactNode;
};

/**
 * A menu item that opens a flyout beside it: with the pointer, or from the keyboard with Enter,
 * Space or the right arrow. The left arrow or Esc closes the flyout and returns to the item.
 */
export function SubMenu({ label, icon, disabled, children }: SubMenuProps) {
  const [open, setOpen] = useState(false);
  const [flip, setFlip] = useState(false); // the flyout opens to the left when the right has no room
  const [lift, setLift] = useState(0); // and moves up by this much when the bottom has no room
  const trigger = useRef<HTMLButtonElement>(null);
  const fly = useRef<HTMLDivElement>(null);
  const focusFirst = useRef(false);

  function show(fromKeyboard: boolean) {
    focusFirst.current = fromKeyboard;
    if (open && fromKeyboard) levelItems(fly.current)[0]?.focus(); // the pointer already opened it
    setOpen(true);
  }

  useLayoutEffect(() => {
    if (!open) {
      setLift(0);
      return;
    }
    const rect = fly.current?.getBoundingClientRect();
    if (rect) {
      setFlip(rect.right > window.innerWidth - MARGIN);
      setLift(Math.max(0, rect.bottom - (window.innerHeight - MARGIN))); // keep the bottom on screen
    }
    if (focusFirst.current) levelItems(fly.current)[0]?.focus();
  }, [open]);

  function onTriggerKey(event: KeyboardEvent) {
    if (disabled) return;
    if (event.key === "ArrowRight") {
      event.preventDefault();
      show(true);
    } else if (event.key === "ArrowLeft" && open) {
      event.preventDefault();
      setOpen(false);
    }
  }

  function onFlyKey(event: KeyboardEvent) {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      event.stopPropagation();
      step(fly.current, event.key === "ArrowDown" ? 1 : -1);
    } else if (event.key === "ArrowLeft" || event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation(); // the outer menu stays open
      setOpen(false);
      trigger.current?.focus();
    }
  }

  return (
    <div
      className="submenu-wrap"
      role="none"
      onMouseEnter={() => !disabled && show(false)}
      onMouseLeave={() => setOpen(false)}
    >
      <button
        ref={trigger}
        type="button"
        role="menuitem"
        className="menu-item"
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        disabled={disabled}
        onClick={() => show(true)}
        onKeyDown={onTriggerKey}
      >
        {icon}
        <span className="menu-item-label">{label}</span>
        <ChevronRight size={14} className="menu-chevron" />
      </button>
      {open && (
        <div
          ref={fly}
          className={flip ? "menu submenu flip" : "menu submenu"}
          role="menu"
          aria-label={label}
          style={lift ? { top: -6 - lift } : undefined}
          onKeyDown={onFlyKey}
        >
          {children}
        </div>
      )}
    </div>
  );
}
