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

// GET and PUT /api/settings: every saved setting, plus the key state of the selected provider.
export type Mode = "local" | "api";
export type ApiProvider = "anthropic" | "openai";
export type ThemeSetting = "light" | "dark" | "system";
export type Accent = "green" | "blue" | "orange" | "purple" | "slate";
export type TextSize = "small" | "default" | "large";
export type Spacing = "comfortable" | "compact";

export type Settings = {
  mode: Mode;
  local_model_id: string | null;
  api_provider: ApiProvider;
  api_model: string;
  api_base_url: string;
  language: "en" | "th";
  theme: ThemeSetting;
  accent: Accent;
  text_size: TextSize;
  spacing: Spacing;
  ask_before_delete: boolean;
  api_key_set: boolean;
  api_key_hint: string | null;
};

/** What PUT /api/settings accepts: any of the saved settings (never the key state). */
export type SettingsChanges = Partial<Omit<Settings, "api_key_set" | "api_key_hint">>;
