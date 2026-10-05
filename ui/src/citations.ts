/** `start` and `end` are offsets in the answer text; a marker's range covers all of "[1, 2]". */
export type Segment =
  | { kind: "text"; text: string; start: number; end: number }
  | { kind: "cite"; n: number; start: number; end: number };

/** One citation marker: [1], [12] or [1, 2]. Shared by the chip splitter and the sentence finder. */
export const MARKER_SOURCE = String.raw`\[(\d{1,2}(?:\s*,\s*\d{1,2})*)\]`;
const MARKER = new RegExp(MARKER_SOURCE, "g");

/** Split answer text into plain text and [n] citations. Numbers without a source stay text. */
export function splitCitations(text: string, sourceCount: number): Segment[] {
  const segments: Segment[] = [];
  let last = 0;
  for (const match of text.matchAll(MARKER)) {
    const numbers = match[1].split(",").map((part) => Number(part.trim()));
    if (numbers.some((n) => n < 1 || n > sourceCount)) continue;
    const start = match.index ?? 0;
    const end = start + match[0].length;
    if (start > last) segments.push({ kind: "text", text: text.slice(last, start), start: last, end: start });
    for (const n of numbers) segments.push({ kind: "cite", n, start, end });
    last = end;
  }
  if (last < text.length) {
    segments.push({ kind: "text", text: text.slice(last), start: last, end: text.length });
  }
  return segments;
}
