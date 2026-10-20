import katex from "katex";

/** `start` and `end` are offsets in the text; a formula's range covers its delimiters. */
export type MathSegment =
  | { kind: "text"; text: string; start: number; end: number }
  | { kind: "math"; src: string; display: boolean; start: number; end: number };

/** A single-dollar span is a formula only if it has TeX in it, so "$5 and $10" stays a price. */
const TEX_MARK = /[\\^_{]/;

type Found = { src: string; display: boolean; end: number };

/** The formula that starts at `i`, or null. An unclosed delimiter is no formula: it stays text. */
function mathAt(text: string, i: number): Found | null {
  const closed = (open: number, close: string, display: boolean): Found | null => {
    const at = text.indexOf(close, i + open);
    const src = at < 0 ? "" : text.slice(i + open, at);
    return src.trim() ? { src, display, end: at + close.length } : null;
  };
  if (text.startsWith("$$", i)) return closed(2, "$$", true);
  if (text.startsWith("\\[", i)) return closed(2, "\\]", true);
  if (text.startsWith("\\(", i)) return closed(2, "\\)", false);
  if (text[i] !== "$") return null;
  // `$...$` has to close on the same line, at a `$` that is not escaped.
  const lineEnd = text.indexOf("\n", i);
  const limit = lineEnd < 0 ? text.length : lineEnd;
  let at = text.indexOf("$", i + 1);
  while (at > 0 && at < limit && text[at - 1] === "\\") at = text.indexOf("$", at + 1);
  if (at < 0 || at >= limit) return null;
  const src = text.slice(i + 1, at);
  return TEX_MARK.test(src) ? { src, display: false, end: at + 1 } : null;
}

/**
 * Split text into plain text and formulas: `$$..$$` and `\[..\]` (display), `\(..\)` and `$..$`
 * (inline). The segments cover the whole text without gaps, so offsets stay those of the raw text.
 */
export function splitMath(text: string): MathSegment[] {
  const segments: MathSegment[] = [];
  let plain = 0;
  let i = 0;
  while (i < text.length) {
    const found = mathAt(text, i);
    if (!found) {
      // `\$` and a `$$` that did not close are skipped whole, so their second `$` opens nothing.
      i += text.startsWith("$$", i) || text.startsWith("\\$", i) ? 2 : 1;
      continue;
    }
    if (i > plain) segments.push({ kind: "text", text: text.slice(plain, i), start: plain, end: i });
    segments.push({ kind: "math", src: found.src, display: found.display, start: i, end: found.end });
    i = plain = found.end;
  }
  if (plain < text.length) {
    segments.push({ kind: "text", text: text.slice(plain), start: plain, end: text.length });
  }
  return segments;
}

/** MathML for a formula: no stylesheet or font is needed. The only HTML KaTeX is given is its own output. */
export function renderMath(src: string, display: boolean): string {
  return katex.renderToString(src, {
    output: "mathml",
    displayMode: display,
    throwOnError: false,
    trust: false,
    strict: "ignore", // Thai or other text inside a formula is allowed, and not worth a console warning
  });
}
