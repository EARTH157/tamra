// JSON shapes of the Tamra core API (src/tamra/server.py).

export type FileCounts = {
  pending: number;
  indexing: number;
  indexed: number;
  failed: number;
  skipped: number;
};

export type ProblemFile = { rel_path: string; status: string; error: string | null };

export type IndexStatus = {
  stale: boolean;
  counts: FileCounts;
  current: string | null;
  error: string | null;
  problems: ProblemFile[];
};

export type Collection = { id: number; name: string; folder_path: string; created_at: string };

export type CollectionState = { collection: Collection | null; index: IndexStatus | null };

export type Chat = { id: number; title: string; created_at: string; updated_at: string };

export type Source = {
  n: number;
  file: string;
  label: string;
  text: string;
  location: Record<string, unknown>;
};

export type Message = {
  id: number;
  role: "user" | "assistant";
  content: string;
  provider: string | null;
  model: string | null;
  created_at: string;
  sources: Source[];
};

export type ChatDetail = Chat & { messages: Message[] };

export type AnswerEvent =
  | { type: "sources"; sources: Source[] }
  | { type: "token"; text: string }
  | { type: "error"; message: string }
  | { type: "done"; message_id: number };
