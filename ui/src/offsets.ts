/**
 * The code-point offset of a UTF-16 index: the inverse of cpToUtf16, for sending an offset into
 * the text to the core, which counts as Python does. An index inside a surrogate pair counts the
 * whole character. Past the end gives the number of code points.
 */
export function utf16ToCp(text: string, index: number): number {
  const end = Math.min(Math.max(index, 0), text.length);
  if (!/[\uD800-\uDFFF]/.test(text)) return end;
  let count = 0;
  let seen = 0;
  for (const char of text) {
    if (seen >= end) break;
    seen += char.length;
    count++;
  }
  return count;
}

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
