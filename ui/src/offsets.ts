/**
 * The UTF-16 index of a code-point offset. The core counts characters as Python does, in code
 * points, while JavaScript strings index UTF-16 units, so an emoji or another character outside
 * the basic plane counts 1 there and 2 here. Past the end gives the string's length.
 */
export function cpToUtf16(text: string, cp: number): number {
  if (!/[\uD800-\uDFFF]/.test(text)) return Math.min(Math.max(cp, 0), text.length);
  let index = 0;
  let seen = 0;
  for (const char of text) {
    if (seen >= cp) break;
    index += char.length;
    seen++;
  }
  return index;
}
