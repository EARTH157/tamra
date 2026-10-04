// The core's reply when the documents do not contain the answer.
// These mirror tamra.answer.NOT_FOUND (src/tamra/answer.py); keep the two in sync.
export const NOT_FOUND_REPLIES: readonly string[] = [
  "ไม่พบข้อมูลนี้ในเอกสาร",
  "Not found in the documents.",
  "文档中未找到相关信息。",
];

/** True for an assistant reply that is the core's not-found answer. */
export function isNotFound(content: string, sourceCount: number): boolean {
  return sourceCount === 0 && NOT_FOUND_REPLIES.includes(content.trim());
}
