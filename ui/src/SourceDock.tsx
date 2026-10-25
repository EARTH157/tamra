import {
  type CSSProperties,
  type PointerEvent,
  type ReactNode,
  type RefObject,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { useT } from "./i18n";
import Splitter from "./Splitter";

/** The docked cards' width at start, and how narrow a card can be made, in CSS pixels. */
export const PANEL_WIDTH = 520;
const PANEL_MIN = 320;
/**
 * How narrow the chat can get as the source column is dragged wider, plus the column's 28px of
 * margin; the column's CSS says it too.
 */
const CHAT_MIN = 428;
/** How short a docked card can be made when two share the column. */
const CARD_MIN_HEIGHT = 160;
/** How far a header is dragged before its card comes away; a shorter move is a click. */
const DRAG_START = 4;
/** How much of a floating card stays over the chat: its header, so it can be dragged back. */
const HEADER_HEIGHT = 65;
/** How short a floating card can be made, and how tall it starts at most. */
const FLOAT_MIN_HEIGHT = 240;
const FLOAT_HEIGHT = 640;
/** Half the height of a tail where it meets its card, and how far that stays from its corners. */
const TAIL_HALF = 20;
const TAIL_INSET = TAIL_HALF + 20;
/** How far out from its card a tail is drawn with the card, over the card's border and shadow. */
const NECK = 16;

/** Where a floating card is, from the chat's top left, and its size, in CSS pixels. */
type FloatAt = { x: number; y: number; w: number; h: number };
/** A floating card's corners, as [left, top]. */
const CORNERS = [
  [true, true],
  [false, true],
  [true, false],
  [false, false],
] as const;

/** What a card's panel is given to be docked or floating. */
export type Place = {
  floating: boolean;
  onToggleFloat: () => void;
  onGrab: (event: PointerEvent<HTMLElement>) => void;
  edges: ReactNode;
};

export type DockCard = {
  key: number;
  /** The most recently used floating card is drawn over the other. */
  used: number;
  /** What opened it, for its tail to point at; null hides the tail. */
  target: () => Element | null;
  panel: (place: Place) => ReactNode;
};

type Props = {
  cards: DockCard[];
  /** The chat view the cards are laid out in, and its scrolling list of messages. */
  root: RefObject<HTMLDivElement | null>;
  list: RefObject<HTMLDivElement | null>;
  /** A card was pressed: it is the one in use. */
  onUse: (key: number) => void;
};

/**
 * Point a card at what opened it with its tail, a wedge from the card's side facing it (the left
 * one while docked) to a point there. It hides while that is scrolled out of view or under the card.
 * `panel` is the card's slot, which the card fills.
 */
function pointTail(
  root: HTMLElement,
  list: HTMLElement,
  target: Element | null,
  panel: HTMLElement,
  tail: SVGElement,
) {
  // A span over several lines is pointed at where it ends.
  const rects = target?.getClientRects();
  const at = rects?.[rects.length - 1];
  const shown = list.getBoundingClientRect();
  const box = panel.getBoundingClientRect();
  const side =
    !at || at.height === 0 || at.bottom <= shown.top || at.top >= shown.bottom
      ? null
      : at.right < box.left
        ? "left"
        : at.left > box.right
          ? "right"
          : at.bottom < box.top
            ? "top"
            : at.top > box.bottom
              ? "bottom"
              : null;
  // Where it meets the card: beside the header on the left or right, so every tail is the header's
  // colour; across from its target on the top or bottom, clear of the round corners.
  const upright = side === "left" || side === "right";
  const [from, to] = upright ? [box.top, box.bottom] : [box.left, box.right];
  const mid = at ? (upright ? (at.top + at.bottom) / 2 : (at.left + at.right) / 2) : 0;
  const centre = upright
    ? box.top + HEADER_HEIGHT / 2 + 4 // below the top corner's curve
    : Math.min(Math.max(mid, from + TAIL_INSET), to - TAIL_INSET);
  // The wedge runs over the chat and under the cards, so the card lies over its base. Its short
  // neck next to the card is drawn again with the card, over the card's border and shadow, so the
  // two read as one shape.
  const neck = panel.querySelector<SVGElement>(":scope > .tail-neck");
  tail.toggleAttribute("data-hidden", !side);
  neck?.toggleAttribute("data-hidden", !side);
  if (!at || !side) return;
  const edge = { left: box.left, right: box.right, top: box.top, bottom: box.bottom }[side];
  const inward = side === "left" || side === "top" ? 1 : -1;
  const tip = {
    left: [at.right + 4, mid],
    right: [at.left - 4, mid],
    top: [mid, at.bottom + 4],
    bottom: [mid, at.top - 4],
  }[side] as Point;
  const xy = (along: number, out: number): Point => (upright ? [out, along] : [along, out]);
  const [a, b] = [xy(centre - TAIL_HALF, edge), xy(centre + TAIL_HALF, edge)];
  const area = root.getBoundingClientRect();
  const [fill, outline] = tail.querySelectorAll("path");
  fill?.setAttribute("d", pathOf([tip, a, b], [area.left, area.top], true));
  outline?.setAttribute("d", pathOf([a, tip, b], [area.left, area.top]));
  if (!neck) return;
  // The neck: the wedge's first NECK pixels out from the card, reaching 2px in over its border.
  const t = Math.min(1, NECK / Math.max(1, Math.abs((upright ? tip[0] : tip[1]) - edge)));
  const toward = ([x, y]: Point): Point => [x + (tip[0] - x) * t, y + (tip[1] - y) * t];
  const inside = ([x, y]: Point): Point => (upright ? [x + 2 * inward, y] : [x, y + 2 * inward]);
  const corner: Point = [box.left, box.top];
  const [neckFill, neckOutline] = neck.querySelectorAll("path");
  neckFill?.setAttribute("d", pathOf([inside(a), toward(a), toward(b), inside(b)], corner, true));
  neckOutline?.setAttribute("d", pathOf([a, toward(a)], corner) + pathOf([b, toward(b)], corner));
}

type Point = [number, number];

/** An SVG path through points given in the window, drawn from `origin`. */
function pathOf(points: Point[], [left, top]: Point, close = false): string {
  return `M${points.map(([x, y]) => `${x - left} ${y - top}`).join("L")}${close ? "Z" : ""}`;
}

/**
 * The open source cards beside the chat: docked in a column at its right (one, or two split top
 * and bottom), or floating over it, each where it was put. A card is dragged out by its header and
 * docked again by pushing it against the chat's right edge, into the top or bottom half.
 */
export default function SourceDock({ cards, root, list, onUse }: Props) {
  const t = useT();
  const [panelWidth, setPanelWidth] = useState(PANEL_WIDTH);
  // The cards out of the column, by key. A card stays where it was put until docked.
  const [floats, setFloats] = useState<Record<number, FloatAt>>({});
  // The order of the docked cards, top first, by key; a card not ranked yet goes at the bottom.
  const [ranks, setRanks] = useState<Record<number, number>>({});
  // The top card's height when two share the column and it was dragged; else they split evenly.
  const [split, setSplit] = useState<number | null>(null);
  // While a card is pushed against the chat's right edge: the slot it docks into when let go.
  const [preview, setPreview] = useState<"top" | "bottom" | null>(null);
  const column = useRef<HTMLDivElement>(null);

  const rank = (key: number) => ranks[key] ?? 1e9 + key;
  const docked = cards.filter((card) => !floats[card.key]).sort((a, b) => rank(a.key) - rank(b.key));
  const floating = cards.filter((card) => floats[card.key]).sort((a, b) => a.used - b.used);

  // The tails follow their targets as the chat renders, scrolls and resizes. A card is measured by
  // its slot: popping in, the card itself is still moved and scaled.
  const current = useRef(cards);
  current.current = cards;
  function pointTails() {
    const area = root.current;
    const messages = list.current;
    if (!area || !messages) return;
    for (const card of current.current) {
      const slot = area.querySelector<HTMLElement>(`.card-slot[data-card="${card.key}"]`);
      const tail = area.querySelector<SVGElement>(`svg.source-tail[data-card="${card.key}"]`);
      if (slot && tail) pointTail(area, messages, card.target(), slot, tail);
    }
  }
  useLayoutEffect(pointTails);

  useEffect(() => {
    const messages = list.current;
    messages?.addEventListener("scroll", pointTails);
    window.addEventListener("resize", pointTails);
    return () => {
      messages?.removeEventListener("scroll", pointTails);
      window.removeEventListener("resize", pointTails);
    };
  }, []);

  const area = () => root.current?.getBoundingClientRect() ?? new DOMRect(0, 0, 1000, 800);
  const setFloat = (key: number, change: (at: FloatAt) => FloatAt) =>
    setFloats((all) => (all[key] ? { ...all, [key]: change(all[key]) } : all));

  /** Back into the column, at the top or the bottom. */
  function dock(key: number, slot: "top" | "bottom") {
    const others = docked.filter((card) => card.key !== key).map((card) => rank(card.key));
    const at = slot === "top" ? Math.min(0, ...others) - 1 : Math.max(0, ...others) + 1;
    setFloats((all) => {
      const rest = { ...all };
      delete rest[key];
      return rest;
    });
    setRanks((all) => ({ ...all, [key]: at }));
  }

  /** A docked card coming away keeps its width, and is as tall as fits below where it goes. */
  function floatSize(box: DOMRect, y: number) {
    return {
      w: Math.max(PANEL_MIN, box.width),
      h: Math.max(FLOAT_MIN_HEIGHT, Math.min(FLOAT_HEIGHT, area().height - y - 16)),
    };
  }

  /** Drag a card by its header: out over the chat, or back into the column at the right edge. */
  function grab(key: number, event: PointerEvent<HTMLElement>) {
    const panel = event.currentTarget.closest<HTMLElement>(".source-panel");
    if (event.button !== 0 || !root.current || !panel) return;
    event.preventDefault(); // no text selection while dragging
    const from = { x: event.clientX, y: event.clientY };
    const box = panel.getBoundingClientRect();
    const start = area();
    const size = floats[key] ?? floatSize(box, box.top - start.top);
    let moved = false;
    let slot: "top" | "bottom" | null = null;
    const move = (e: globalThis.PointerEvent) => {
      if (!moved && Math.hypot(e.clientX - from.x, e.clientY - from.y) < DRAG_START) return;
      moved = true;
      const bounds = area();
      const x = box.left + e.clientX - from.x - bounds.left;
      // Pushed all the way to the chat's right edge, the column makes room for it.
      slot = x + size.w < bounds.width ? null : e.clientY - bounds.top < bounds.height / 2 ? "top" : "bottom";
      setPreview(slot);
      setFloats((all) => ({
        ...all,
        [key]: {
          x: Math.max(0, Math.min(x, bounds.width - size.w)),
          y: Math.max(0, Math.min(box.top + e.clientY - from.y - bounds.top, bounds.height - HEADER_HEIGHT)),
          w: size.w,
          h: size.h,
        },
      }));
    };
    const end = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", end);
      window.removeEventListener("pointercancel", end);
      setPreview(null);
      if (slot) dock(key, slot);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", end);
    window.addEventListener("pointercancel", end);
  }

  /** The header's pop-out / dock button: popped out, a card moves a little from where it was. */
  function toggleFloat(key: number) {
    if (floats[key]) return dock(key, "bottom");
    const panel = root.current?.querySelector(`[data-card="${key}"] .source-panel`);
    if (!panel) return;
    const bounds = area();
    const box = panel.getBoundingClientRect();
    const x = Math.max(0, box.left - bounds.left - 24);
    const y = Math.max(0, box.top - bounds.top + 24);
    setFloats((all) => ({ ...all, [key]: { x, y, ...floatSize(box, y) } }));
  }

  /** Drag a corner of a floating card: it resizes both ways, its opposite corner staying put. */
  function grabCorner(key: number, event: PointerEvent<HTMLElement>, left: boolean, top: boolean) {
    const at = floats[key];
    if (event.button !== 0 || !at) return;
    event.preventDefault(); // no text selection while dragging
    const from = { x: event.clientX, y: event.clientY };
    const bounds = area();
    const clamp = (value: number, min: number, max: number) => Math.min(Math.max(value, min), max);
    const move = (e: globalThis.PointerEvent) => {
      const [dx, dy] = [e.clientX - from.x, e.clientY - from.y];
      const w = clamp(at.w + (left ? -dx : dx), PANEL_MIN, left ? at.x + at.w : bounds.width - at.x);
      const h = clamp(at.h + (top ? -dy : dy), FLOAT_MIN_HEIGHT, top ? at.y + at.h : bounds.height - at.y);
      setFloat(key, () => ({ x: left ? at.x + at.w - w : at.x, y: top ? at.y + at.h - h : at.y, w, h }));
    };
    const end = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", end);
      window.removeEventListener("pointercancel", end);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", end);
    window.addEventListener("pointercancel", end);
  }

  /** The edges of a floating card: the left one moves it as it resizes, the right one does not. */
  function edges(key: number, at: FloatAt) {
    return (
      <>
        <div className="float-edge left">
          <Splitter
            label={t("chat.resizeSource")}
            orientation="vertical"
            size={at.w}
            min={PANEL_MIN}
            max={() => at.x + at.w}
            grow={-1}
            onResize={(w) => setFloat(key, (f) => ({ ...f, x: f.x + f.w - w, w }))}
          />
        </div>
        <div className="float-edge right">
          <Splitter
            label={t("chat.resizeSource")}
            orientation="vertical"
            size={at.w}
            min={PANEL_MIN}
            max={() => area().width - at.x}
            grow={1}
            onResize={(w) => setFloat(key, (f) => ({ ...f, w }))}
          />
        </div>
        <div className="float-edge bottom">
          <Splitter
            label={t("chat.resizeSourceHeight")}
            orientation="horizontal"
            size={at.h}
            min={FLOAT_MIN_HEIGHT}
            max={() => area().height - at.y}
            grow={1}
            onResize={(h) => setFloat(key, (f) => ({ ...f, h }))}
          />
        </div>
        {CORNERS.map(([left, top]) => (
          <div
            key={`${left}${top}`}
            className={`float-corner ${top ? "top" : "bottom"} ${left ? "left" : "right"}`}
            aria-hidden="true" // the edges resize it from the keyboard
            onPointerDown={(event) => grabCorner(key, event, left, top)}
          />
        ))}
      </>
    );
  }

  const place = (key: number): Place => ({
    floating: !!floats[key],
    onToggleFloat: () => toggleFloat(key),
    onGrab: (event) => grab(key, event),
    edges: floats[key] ? edges(key, floats[key]) : null,
  });

  // The column's slots, top first: its docked cards, and where a card pushed against the edge goes.
  const zone = <div key="zone" className="dock-zone" aria-hidden="true" />;
  const slots: ReactNode[] = docked.map((card) => (
    <div key={card.key} className="card-slot" data-card={card.key} onPointerDownCapture={() => onUse(card.key)}>
      {card.panel(place(card.key))}
      <svg className="tail-neck" aria-hidden="true">
        <path className="fill" />
        <path className="outline" />
      </svg>
    </div>
  ));
  if (preview) slots.splice(preview === "top" ? 0 : slots.length, 0, zone);
  // Two docked cards (not while one is being docked) split the column where it was dragged.
  const sized = docked.length === 2 && !preview && split !== null;
  if (docked.length === 2 && !preview) {
    slots.splice(
      1,
      0,
      <Splitter
        key="split"
        label={t("chat.resizeSourceSplit")}
        orientation="horizontal"
        size={split ?? (column.current?.clientHeight ?? 600) / 2}
        min={CARD_MIN_HEIGHT}
        max={() => (column.current?.clientHeight ?? 600) - CARD_MIN_HEIGHT}
        grow={1}
        onResize={setSplit}
      />,
    );
  }

  return (
    <>
      {slots.length > 0 && (
        <>
          <Splitter
            label={t("chat.resizeSource")}
            orientation="vertical"
            size={panelWidth}
            min={PANEL_MIN}
            max={() => (root.current?.clientWidth ?? 1000) - CHAT_MIN}
            grow={-1}
            onResize={setPanelWidth}
          />
          <div
            className={sized ? "source-column sized" : "source-column"}
            ref={column}
            style={{ "--panel-width": `${panelWidth}px`, "--split": `${split}px` } as CSSProperties}
          >
            {slots}
          </div>
        </>
      )}
      {floating.map((card, index) => {
        const at = floats[card.key];
        return (
          <div
            key={card.key}
            className="card-slot floating"
            data-card={card.key}
            style={
              {
                "--float-x": `${at.x}px`,
                "--float-y": `${at.y}px`,
                "--float-w": `${at.w}px`,
                "--float-h": `${at.h}px`,
                zIndex: 20 + index,
              } as CSSProperties
            }
            onPointerDownCapture={() => onUse(card.key)}
          >
            {card.panel(place(card.key))}
            <svg className="tail-neck" aria-hidden="true">
              <path className="fill" />
              <path className="outline" />
            </svg>
          </div>
        );
      })}
      {cards.map((card) => (
        <svg key={card.key} className="source-tail" data-card={card.key} aria-hidden="true">
          <path className="fill" />
          <path className="outline" />
        </svg>
      ))}
    </>
  );
}
