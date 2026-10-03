export type Segment = { kind: "text"; text: string } | { kind: "cite"; n: number };

const MARKER = /\[(\d{1,2}(?:\s*,\s*\d{1,2})*)\]/g;

/** Split answer text into plain text and [n] citations. Numbers without a source stay text. */
export function splitCitations(text: string, sourceCount: number): Segment[] {
  const segments: Segment[] = [];
  let last = 0;
  for (const match of text.matchAll(MARKER)) {
    const numbers = match[1].split(",").map((part) => Number(part.trim()));
    if (numbers.some((n) => n < 1 || n > sourceCount)) continue;
    const start = match.index ?? 0;
    if (start > last) segments.push({ kind: "text", text: text.slice(last, start) });
    for (const n of numbers) segments.push({ kind: "cite", n });
    last = start + match[0].length;
  }
  if (last < text.length) segments.push({ kind: "text", text: text.slice(last) });
  return segments;
}
