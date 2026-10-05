import { MARKER_SOURCE } from "./citations";

/** The shortest and longest selection that can be checked, after trimming (the core's limits). */
export const MIN_SELECTION = 2;
export const MAX_SELECTION = 2000;

/** A selected part of an answer: offsets in the message content, and that exact substring. */
export type Selected = { text: string; start: number; end: number };

type Edge = "start" | "end";

/** The element of the answer text that carries content offsets (data-start/data-end), if any. */
function carrier(node: Node, root: HTMLElement): HTMLElement | null {
  const element = node instanceof HTMLElement ? node : node.parentElement;
  const found = element?.closest<HTMLElement>("[data-start]") ?? null;
  return found && root.contains(found) ? found : null;
}

function offsetsOf(element: HTMLElement): [number, number] {
  return [Number(element.dataset.start), Number(element.dataset.end)];
}

/** The node itself when it carries offsets, then every carrier below it, in document order. */
function carriersIn(node: Node): HTMLElement[] {
  if (!(node instanceof HTMLElement)) return [];
  const below = [...node.querySelectorAll<HTMLElement>("[data-start]")];
  return node.matches("[data-start]") ? [node, ...below] : below;
}

/**
 * The content offset of a DOM boundary inside the answer text. The rendered text is the content
 * minus the paragraph breaks and with each [n] marker shown as a chip, so offsets are read from
 * the data-start/data-end of the elements that were rendered from the content.
 */
function contentOffset(node: Node, offset: number, root: HTMLElement): number | null {
  const own = carrier(node, root);
  if (own) {
    const [start, end] = offsetsOf(own);
    if (own.dataset.cite !== undefined) return offset > 0 ? end : start; // a chip is all or nothing
    if (node instanceof Text) return start + Math.min(offset, end - start);
    return offset > 0 ? end : start;
  }
  // A boundary between children (the root itself, for example): the next carrier's start, else the
  // previous carrier's end.
  const children = [...node.childNodes];
  for (let i = offset; i < children.length; i++) {
    const first = carriersIn(children[i])[0];
    if (first) return offsetsOf(first)[0];
  }
  for (let i = Math.min(offset, children.length) - 1; i >= 0; i--) {
    const last = carriersIn(children[i]).at(-1);
    if (last) return offsetsOf(last)[1];
  }
  return null;
}

/**
 * The selection when it lies inside one answer's text. `root` is the element that holds the
 * rendered text (AnswerText) and `content` the message content it was rendered from. A selection
 * that reaches into another message gives null, and so does one that is collapsed, or shorter
 * than MIN_SELECTION or longer than MAX_SELECTION once trimmed. Offsets are mapped back through the
 * citation chips, so `text` is always the raw substring of `content`.
 */
export function selectionInside(root: HTMLElement, content: string): Selected | null {
  const selection = window.getSelection();
  if (!selection || selection.rangeCount === 0 || selection.isCollapsed) return null;
  const range = selection.getRangeAt(0);
  const scope = root.closest(".message") ?? root; // the thinking block and the cards share it
  const edge = (container: Node, offset: number, which: Edge): number | null => {
    if (root.contains(container)) return contentOffset(container, offset, root);
    if (!scope.contains(container)) return null;
    // Outside the text but in the same message: before it, after it, or an ancestor boundary.
    let after: boolean;
    if (container.contains(root)) {
      let branch: Node = root;
      while (branch.parentNode !== container) branch = branch.parentNode as Node;
      const index = [...container.childNodes].indexOf(branch as ChildNode);
      after = offset > index;
    } else {
      after = !!(root.compareDocumentPosition(container) & Node.DOCUMENT_POSITION_FOLLOWING);
    }
    if (which === "start") return after ? null : 0;
    return after ? content.length : null;
  };
  let start = edge(range.startContainer, range.startOffset, "start");
  let end = edge(range.endContainer, range.endOffset, "end");
  if (start === null || end === null) return null;
  while (start < end && /\s/.test(content[start])) start++;
  while (end > start && /\s/.test(content[end - 1])) end--;
  if (end - start < MIN_SELECTION || end - start > MAX_SELECTION) return null;
  return { text: content.slice(start, end), start, end };
}

const MARKER_AT_END = new RegExp(`(?:\\s+|${MARKER_SOURCE})$`);
const MARKER_ENDING = new RegExp(`${MARKER_SOURCE}$`);
const TERMINATOR = /[.!?]/;
const CJK_TERMINATOR = /[。！？]/;

/**
 * The sentence that ends at the citation marker starting at `chipIndex`: the text back to the
 * previous sentence end, line break or citation marker. Markers and spaces right before the chip
 * (the "[1]" of "[1][2]") are skipped; a final full stop belongs to the sentence. Thai has no
 * full stop, so a line break, a previous marker or the start of the text ends it too. A sentence
 * longer than MAX_SELECTION is cut to its last MAX_SELECTION characters. Empty (start === end)
 * when there is no text before the chip.
 */
export function sentenceBefore(content: string, chipIndex: number): { start: number; end: number } {
  let end = chipIndex;
  for (;;) {
    const trailing = MARKER_AT_END.exec(content.slice(0, end));
    if (!trailing) break;
    end -= trailing[0].length;
  }
  let probe = end;
  while (probe > 0 && (TERMINATOR.test(content[probe - 1]) || CJK_TERMINATOR.test(content[probe - 1]))) {
    probe--;
  }
  let start = 0;
  for (let i = probe - 1; i >= 0; i--) {
    const ch = content[i];
    const next = content[i + 1];
    if (
      ch === "\n" ||
      CJK_TERMINATOR.test(ch) ||
      (TERMINATOR.test(ch) && next !== undefined && /\s/.test(next)) ||
      (ch === "]" && MARKER_ENDING.test(content.slice(0, i + 1)))
    ) {
      start = i + 1;
      break;
    }
  }
  while (start < end && /\s/.test(content[start])) start++;
  return { start: Math.max(start, end - MAX_SELECTION), end };
}
