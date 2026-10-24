// JSON shapes of the Tamra core API (src/tamra/server.py).

export type FileCounts = {
  pending: number;
  indexing: number;
  indexed: number;
  failed: number;
  skipped: number;
};

export type ProblemFile = {
  rel_path: string;
  status: string;
  error: string | null;
  /** Scanned pages wait for the OCR model. */
  needs_ocr?: boolean;
};

export type IndexStatus = {
  stale: boolean;
  counts: FileCounts;
  current: string | null;
  error: string | null;
  /** The page OCR is reading in the current file. */
  reading?: { page: number; pages: number } | null;
  problems: ProblemFile[];
};

/** A project: a name and one folder of documents with its own search index (GET /api/projects). */
export type Project = { id: number; name: string; folder_path: string; notes: string; index: IndexStatus };

export type Chat = {
  id: number;
  title: string;
  created_at: string;
  updated_at: string;
  /** The home project, where the chat is listed; null only for a chat made before any project. */
  project_id: number | null;
  /** The ids of the projects the chat searches. */
  scope: number[];
};

/** A chat a search found, with an excerpt of its newest message that matched (null: the title did). */
export type ChatMatch = Chat & { match: string | null };

export type Source = {
  n: number;
  /** The file's id in the index; null once it left the index (absent in payloads before M3). */
  file_id?: number | null;
  file: string;
  label: string;
  text: string;
  location: Record<string, unknown>;
  /** The project the file belongs to; null once the file left the index (absent before M4). */
  project?: string | null;
};

/** One place in a source's snapshot that an answer span matches (POST /api/attribution). */
export type Match = {
  n: number;
  /** Character offsets into the source's snapshot text; `text` is that slice. */
  start: number;
  end: number;
  text: string;
  label: "strong" | "partial";
  file_id: number | null;
  file: string;
  location_label: string;
  /** The file is gone from the index or differs from what the answer saw. */
  changed: boolean;
};

export type AttributionResult = { matches: Match[] };

/** GET /api/sources/{message_id}/{n}/locate: where a snapshot passage is in the current file. */
export type Locate = {
  file_id: number;
  kind: "pdf" | "text" | "docx";
  changed: boolean;
  found: boolean;
  /** PDF only. */
  page_count?: number;
  page?: number;
  /** Fractions of the page, top-left origin: [x0, y0, x1, y1]. */
  rects?: number[][];
  /** PDF only, when asked with a focus: where the focused text is in the passage, in the same form. */
  focus_rects?: number[][];
  /** Text: 1-based lines; DOCX: paragraph indices. The end is exclusive. For a PDF, offsets in the page text. */
  start?: number;
  end?: number;
  /** PDF only: the page was read by OCR. */
  ocr?: boolean;
};

/** What the viewer needs to open a file at a source passage (the Task 5 hand-off). */
export type ViewerRequest = {
  messageId: number;
  n: number;
  fileId: number;
  file: string;
  /** Snapshot offsets of the passage; null for the whole snapshot. */
  start: number | null;
  end: number | null;
  /** The answer text that was checked (the selection, or the sentence before a chip); null when none was. */
  selection: string | null;
};

/** GET /api/files/{id}/text: a text or Markdown file by line, or a DOCX by paragraph. */
export type TextDoc =
  | { kind: "text"; lines: string[]; truncated?: boolean }
  | {
      kind: "docx";
      paragraphs: { index: number; text: string; heading: boolean }[];
      truncated?: boolean;
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

/** What a prompt puts in the context window, part by part (estimated), and the reply room. */
export type ContextEvent = {
  type: "context";
  prompt_tokens: number;
  context_tokens: number;
  reply_tokens: number;
  /** `notes` is missing from a context saved before project notes; read it as 0. */
  parts: { instructions: number; notes?: number; sources: number; history: number; question: number };
  source_count: number;
  history_kept: number;
  history_total: number;
};

/** The chat's last context event, saved with the local model's final counts (null without). */
export type SavedContext = Omit<ContextEvent, "type"> & {
  measured: { prompt_tokens: number; generated_tokens: number } | null;
};

export type ChatDetail = Chat & { messages: Message[]; context: SavedContext | null };

/** How Tamra responds: write a cited answer, only show the matching passages, or chat with the
 * model without searching. */
export type AnswerMode = "answer" | "search" | "chat";

/** Why a model call failed (ProviderError.reason); errors that are not the model's carry none. */
export type ErrorReason =
  | "offline"
  | "auth"
  | "quota"
  | "model_missing"
  | "model_unsupported"
  | "embedding_missing"
  | "other";

export type AnswerEvent =
  | { type: "sources"; sources: Source[] }
  | { type: "notice"; code: "stale_projects"; names: string[] }
  | { type: "thinking"; text: string }
  | { type: "token"; text: string }
  | ContextEvent
  // The local model's own counts so far: the prompt, and the tokens it has generated.
  | { type: "usage"; prompt_tokens: number; generated_tokens: number; tokens_per_second: number }
  | { type: "error"; message: string; reason?: ErrorReason }
  | { type: "done"; message_id: number };

// GET /api/models.
export type ModelState = "idle" | "downloading" | "verifying" | "error";

/** What every catalog entry reports: its files and where a download of them stands. */
export type ModelStatus = {
  id: string;
  name: string;
  size: number;
  installed: boolean;
  state: ModelState;
  done: number;
  total: number;
  error: string | null;
};

/** The search (embedding) model; the core runs it on this computer for every project. */
export type EmbeddingModel = ModelStatus;

export type LocalModel = ModelStatus & {
  tier: "small" | "medium" | "large";
  recommended: boolean;
  license: string;
  languages: string[];
  context_length: number;
  thinking: boolean;
  /** The video memory the model needs to run well; 0 for a model that runs on the CPU. */
  min_vram_gb: number;
  /** Where the installed copy is; null while the model is not installed. */
  source: ModelSource | null;
};

/** Where a local model file is: Tamra's own folder, a file the user linked, or another tool. */
export type ModelSource = "tamra" | "linked" | "ollama" | "lmstudio" | "huggingface";

export type UncataloguedModel = {
  id: string;
  name: string;
  file: string;
  size: number;
  source: ModelSource;
  /** The full path of the file (shown as a tooltip; sent back to unlink a linked file). */
  path: string;
};

/** `integrated` is a GPU that shares the computer's memory (an iGPU). */
export type Gpu = { name: string; vram_mb: number; integrated: boolean };

export type ModelsInfo = {
  hardware: { ram_gb: number; gpus: Gpu[] };
  recommended_tier: "small" | "medium" | "large";
  /** `id` is the local model that local mode would use, in either mode. */
  active: { mode: Mode; label: string | null; id: string | null };
  /** The local model's context window as set, and what "auto" picks; null with no local model. */
  context: { tokens: number; auto: number } | null;
  /** Whether the running local model uses the GPU; null when none is running or it is unknown. */
  gpu_offload: boolean | null;
  local: LocalModel[];
  uncatalogued: UncataloguedModel[];
  embedding: EmbeddingModel;
  /** The reranker; null when the catalog has none. */
  reranker: ModelStatus | null;
  /** The OCR model; null when the catalog has none. */
  ocr: ModelStatus | null;
};

/** The tabs of the Settings page. */
export type SettingsTab = "general" | "model" | "style";

// GET and PUT /api/settings: every saved setting, plus the key state of the selected provider.
export type Mode = "local" | "api";
export type ApiProvider = "anthropic" | "openai";
export type ThemeSetting = "light" | "dark" | "system";
export type Accent = "green" | "blue" | "orange" | "purple" | "slate";
export type TextSize = "small" | "default" | "large";
export type Spacing = "comfortable" | "compact";

/** The local model's context window in tokens, or "auto" to fit this computer. */
export type LocalContext = "auto" | "8192" | "16384" | "32768" | "65536";

export type Settings = {
  mode: Mode;
  local_model_id: string | null;
  local_context: LocalContext;
  api_provider: ApiProvider;
  api_model: string;
  api_base_url: string;
  language: "en" | "th";
  theme: ThemeSetting;
  accent: Accent;
  text_size: TextSize;
  spacing: Spacing;
  ask_before_delete: boolean;
  /** AI apps (MCP) may search every project while Tamra is open. */
  mcp_enabled: boolean;
  api_key_set: boolean;
  api_key_hint: string | null;
  /** Where Tamra keeps its data (read-only; the core's `data_dir`). */
  data_dir: string;
  /** How an AI app starts Tamra's MCP server: the command, then its arguments (read-only). */
  mcp_command: string[];
};

/** The answer of POST /api/settings/test-connection (always 200). */
export type ConnectionTest = { ok: boolean; reason: ErrorReason | null; message: string };

/** The answer of POST /api/models/import. */
export type ImportResult = {
  id: string;
  path: string;
  catalogued: boolean;
  /** Used where it is rather than copied into Tamra's models folder. */
  linked: boolean;
  warning: string | null;
};

/** What PUT /api/settings accepts: any of the saved settings (never the key state or data_dir). */
export type SettingsChanges = Partial<
  Omit<Settings, "api_key_set" | "api_key_hint" | "data_dir" | "mcp_command">
>;
