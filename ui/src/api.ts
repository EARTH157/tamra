import { translateNow } from "./i18n";
import { createSseParser } from "./sse";
import type { AnswerEvent, AnswerMode, AttributionResult, Locate } from "./types";

const STORAGE_KEY = "tamra.token";
let token = "";

/** The per-launch API token in a `#token=` URL fragment; null if absent or malformed. */
export function tokenFromHash(hash: string): string | null {
  const match = /(?:^#|&)token=([^&]*)/.exec(hash);
  if (!match) return null;
  try {
    return decodeURIComponent(match[1]);
  } catch {
    return null;
  }
}

/**
 * Read the token once at startup and remove it from the address bar. It is kept in
 * sessionStorage so that reloading the window keeps working.
 */
export function initToken(): void {
  const fromHash = tokenFromHash(window.location.hash);
  if (window.location.hash) {
    history.replaceState(null, "", window.location.pathname + window.location.search);
  }
  try {
    if (fromHash !== null) sessionStorage.setItem(STORAGE_KEY, fromHash);
    token = fromHash ?? sessionStorage.getItem(STORAGE_KEY) ?? "";
  } catch {
    token = fromHash ?? ""; // storage unavailable: a reload will need the token again
  }
}

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function errorOf(response: Response): Promise<ApiError> {
  let message = `HTTP ${response.status}`;
  try {
    const data: unknown = await response.json();
    if (data && typeof data === "object" && "detail" in data && typeof data.detail === "string") {
      message = data.detail;
    }
  } catch {
    // not JSON: keep the status
  }
  return new ApiError(response.status, message);
}

/** Call the Tamra core: JSON in and out, undefined for 204, ApiError on failure. */
export async function api<T>(
  method: string,
  path: string,
  body?: unknown,
  fetchImpl: typeof fetch = fetch,
): Promise<T> {
  const headers: Record<string, string> = { "X-Tamra-Token": token };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetchImpl(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) throw await errorOf(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export type AskOptions = { mode: AnswerMode; think: boolean };

/** Ask a question in a chat and call onEvent for every event of the streamed answer. */
export async function streamAnswer(
  chatId: number,
  content: string,
  onEvent: (event: AnswerEvent) => void,
  options: AskOptions = { mode: "answer", think: false },
  fetchImpl: typeof fetch = fetch,
): Promise<void> {
  const response = await fetchImpl(`/api/chats/${chatId}/messages`, {
    method: "POST",
    headers: { "X-Tamra-Token": token, "Content-Type": "application/json" },
    body: JSON.stringify({ content, mode: options.mode, think: options.think }),
  });
  if (!response.ok) throw await errorOf(response);
  if (!response.body) throw new ApiError(response.status, translateNow("chat.streamEmpty"));
  const feed = createSseParser((data) => onEvent(JSON.parse(data) as AnswerEvent));
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    feed(decoder.decode(value, { stream: true }));
  }
  feed(decoder.decode());
}

/** Find where a selected part of a saved answer comes from, among that answer's own sources. */
export function attribute(
  messageId: number,
  selection: string,
  n: number | null,
): Promise<AttributionResult> {
  return api<AttributionResult>("POST", "/api/attribution", {
    message_id: messageId,
    selection,
    ...(n === null ? {} : { n }),
  });
}

/** Where a passage of a source's snapshot is in the current file (the whole snapshot if no range). */
export function locateSource(
  messageId: number,
  n: number,
  range?: { start: number; end: number },
): Promise<Locate> {
  const query = range ? `?start=${range.start}&end=${range.end}` : "";
  return api<Locate>("GET", `/api/sources/${messageId}/${n}/locate${query}`);
}
