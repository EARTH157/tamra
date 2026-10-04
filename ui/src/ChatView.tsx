import { BookOpen, FileText, Lightbulb, SearchX, X } from "lucide-react";
import { type ReactNode, useEffect, useRef, useState } from "react";
import AnswerText, { TextWithParagraphs } from "./AnswerText";
import { api, streamAnswer } from "./api";
import Composer from "./Composer";
import { useT } from "./i18n";
import { canThink, useModels } from "./models";
import { isNotFound } from "./notFound";
import { useSettings } from "./settings";
import Thinking, { type Thought, thoughtSeconds } from "./Thinking";
import type {
  AnswerMode,
  ChatDetail,
  ErrorReason,
  Message,
  SettingsChanges,
  SettingsTab,
  Source,
} from "./types";

/** A message for the user, with the reason the model call failed when the core gave one. */
type Notice = { message: string; reason?: ErrorReason };

type Pending = {
  question: string;
  mode: AnswerMode;
  sources: Source[];
  text: string;
  thought: Thought | null;
  error: Notice | null;
  done: boolean;
  messageId: number | null;
};

/** The thought is over once the answer starts, ends or fails. */
function endThought(thought: Thought | null): Thought | null {
  return thought && thought.endedAt === null ? { ...thought, endedAt: Date.now() } : thought;
}

/** The open source and the answer it belongs to ("pending" for the one being streamed). */
type Opened = { owner: number | "pending"; source: Source };

type Props = {
  chatId: number | null;
  collectionName?: string;
  createChat: () => Promise<number>;
  onBusyChange: (busy: boolean) => void;
  onAnswered: () => void;
  /** Open Settings on a tab: "Manage models…" and the "no model" error lead to "model". */
  onOpenSettings: (tab: SettingsTab) => void;
  /** False while Settings covers the chat; the models are reloaded when it shows again. */
  visible?: boolean;
};

function sourceOf(sources: Source[], n: number): Source | null {
  return sources.find((source) => source.n === n) ?? null;
}

/**
 * A source opened while its answer was streaming stays open once the answer is saved, when the
 * saved answer still has that source; it then belongs to the saved message.
 */
function reopen(source: Source, saved: ChatDetail | null): Opened | null {
  const answer = [...(saved?.messages ?? [])].reverse().find((m) => m.role === "assistant");
  const same = answer?.sources.find(
    (s) => s.n === source.n && s.file === source.file && s.text === source.text,
  );
  return answer && same ? { owner: answer.id, source: same } : null;
}

/** One chat: its messages, the answer being streamed, the question box, and a source panel. */
export default function ChatView({
  chatId,
  collectionName,
  createChat,
  onBusyChange,
  onAnswered,
  onOpenSettings,
  visible = true,
}: Props) {
  const t = useT();
  const { settings, update } = useSettings();
  const { models, refresh: refreshModels } = useModels(settings);
  const collection = collectionName ?? t("chat.defaultCollection");
  const [detail, setDetail] = useState<ChatDetail | null>(null);
  const [question, setQuestion] = useState("");
  const [mode, setMode] = useState<AnswerMode>("answer");
  const [think, setThink] = useState(false);
  const [pending, setPending] = useState<Pending | null>(null);
  // The reasoning of the answers given in this chat since it was opened, by message id. The core
  // does not save it, so a chat that is loaded again shows none.
  const [thoughts, setThoughts] = useState<Record<number, Thought>>({});
  const [notice, setNotice] = useState<Notice | null>(null);
  const [opened, setOpened] = useState<Opened | null>(null);
  const asking = useRef(false);
  const scroller = useRef<HTMLDivElement>(null);
  const thinkAvailable = canThink(models, settings);

  // A model may have been downloaded or imported in Settings meanwhile.
  const wasVisible = useRef(visible);
  useEffect(() => {
    if (visible && !wasVisible.current) void refreshModels();
    wasVisible.current = visible;
  }, [visible, refreshModels]);

  useEffect(() => {
    if (asking.current) return; // this chat was just created for the question being answered
    setOpened(null);
    setNotice(null);
    setThoughts({});
    setDetail(null);
    if (chatId === null) return;
    let current = true;
    api<ChatDetail>("GET", `/api/chats/${chatId}`)
      .then((loaded) => {
        if (current) setDetail(loaded);
      })
      .catch((e: Error) => {
        if (current) setNotice({ message: e.message });
      });
    return () => {
      current = false;
    };
  }, [chatId]);

  // Keep the newest message in view.
  useEffect(() => {
    const el = scroller.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [detail, pending, notice]);

  async function ask() {
    const text = question.trim();
    if (!text || asking.current) return;
    asking.current = true;
    setQuestion("");
    setNotice(null);
    setOpened(null);
    let state: Pending = {
      question: text,
      mode,
      sources: [],
      text: "",
      thought: null,
      error: null,
      done: false,
      messageId: null,
    };
    setPending(state);
    onBusyChange(true);
    let usedId: number | null = chatId;
    try {
      usedId = chatId ?? (await createChat());
      await streamAnswer(
        usedId,
        text,
        (e) => {
          if (e.type === "sources") {
            state = { ...state, sources: e.sources };
          } else if (e.type === "thinking") {
            const thought = state.thought ?? { text: "", startedAt: Date.now(), endedAt: null };
            state = { ...state, thought: { ...thought, text: thought.text + e.text } };
          } else if (e.type === "token") {
            state = { ...state, text: state.text + e.text, thought: endThought(state.thought) };
          } else if (e.type === "error") {
            state = {
              ...state,
              error: { message: e.message, reason: e.reason },
              thought: endThought(state.thought),
            };
          } else if (e.type === "done") {
            state = {
              ...state,
              done: true,
              messageId: e.message_id,
              thought: endThought(state.thought),
            };
          }
          setPending(state);
        },
        { mode, think: think && thinkAvailable },
      );
    } catch (e) {
      state = { ...state, error: { message: (e as Error).message } };
    } finally {
      const saved = usedId !== null ? await reload(usedId) : null;
      asking.current = false;
      const thought = endThought(state.thought);
      const savedId = state.messageId;
      if (thought && savedId !== null) setThoughts((all) => ({ ...all, [savedId]: thought }));
      setNotice(state.error);
      setPending(null);
      setOpened((open) => (open?.owner === "pending" ? reopen(open.source, saved) : open));
      onBusyChange(false);
      onAnswered();
    }
  }

  /** Show what the core saved for this chat. A chat with nothing loaded stays usable, but empty. */
  async function reload(id: number): Promise<ChatDetail | null> {
    try {
      const loaded = await api<ChatDetail>("GET", `/api/chats/${id}`);
      setDetail(loaded);
      return loaded;
    } catch {
      setDetail(
        (current) =>
          current ?? { id, title: "", created_at: "", updated_at: "", messages: [] },
      );
      return null;
    }
  }

  function stop() {
    api("POST", "/api/answer/cancel").catch((e: Error) => setNotice({ message: e.message }));
  }

  /** Save the model choice. The menu follows the settings; a refusal is shown as a notice. */
  function chooseModel(changes: SettingsChanges) {
    update(changes).catch((e: Error) => setNotice({ message: e.message }));
  }

  const messages: Message[] = detail?.messages ?? [];
  const ready = chatId === null || detail !== null;
  const empty = ready && messages.length === 0 && !pending && !notice;
  const lastAnswer = [...messages].reverse().find((message) => message.role === "assistant");
  const activeFor = (owner: number | "pending") =>
    opened?.owner === owner ? opened.source.n : null;

  return (
    <div className="chat-view">
      <div className="chat-column">
        <div className="messages" ref={scroller}>
          {empty && (
            <div className="hero">
              <span className="hero-tile">
                <BookOpen size={26} />
              </span>
              <h1>{t("chat.emptyTitle")}</h1>
              <p className="hero-sub">{t("chat.emptyText")}</p>
            </div>
          )}
          {messages.map((message) =>
            message.role === "user" ? (
              <div key={message.id} className="message user">
                {message.content}
              </div>
            ) : (
              <Answer
                key={message.id}
                text={message.content}
                sources={message.sources}
                thinking={
                  thoughts[message.id] && <Thinking thought={thoughts[message.id]} />
                }
                meta={metaOf(message, thoughts[message.id], t)}
                hint={message === lastAnswer}
                collectionName={collection}
                active={activeFor(message.id)}
                onOpen={(source) => setOpened({ owner: message.id, source })}
              />
            ),
          )}
          {pending && (
            <>
              <div className="message user">{pending.question}</div>
              {pending.text ||
              pending.done ||
              (pending.mode === "search" && pending.sources.length > 0) ? (
                <Answer
                  text={pending.text}
                  sources={pending.sources}
                  thinking={pending.thought && <Thinking thought={pending.thought} />}
                  meta={null}
                  hint={false}
                  collectionName={collection}
                  done={pending.done}
                  active={activeFor("pending")}
                  onOpen={(source) => setOpened({ owner: "pending", source })}
                />
              ) : (
                !pending.error && (
                  <div className="message assistant">
                    {pending.thought ? (
                      <Thinking thought={pending.thought} />
                    ) : (
                      <p className="searching">
                        <Lightbulb size={16} />
                        {t("chat.searching")}
                      </p>
                    )}
                  </div>
                )
              )}
            </>
          )}
          {notice && (
            <div className="chat-error" role="alert">
              <span>{noticeText(notice, t)}</span>
              {notice.reason === "model_missing" && (
                <button type="button" className="btn" onClick={() => onOpenSettings("model")}>
                  {t("chat.openModelSettings")}
                </button>
              )}
            </div>
          )}
        </div>
        <Composer
          question={question}
          onQuestionChange={setQuestion}
          onSubmit={() => void ask()}
          onStop={stop}
          disabled={!ready}
          answering={pending !== null}
          narrow={empty}
          mode={mode}
          onModeChange={setMode}
          think={think}
          onThinkChange={setThink}
          thinkAvailable={thinkAvailable}
          models={models}
          refreshModels={refreshModels}
          settings={settings}
          onChooseModel={chooseModel}
          onManageModels={() => onOpenSettings("model")}
        />
      </div>
      {opened && <SourcePanel source={opened.source} onClose={() => setOpened(null)} />}
    </div>
  );
}

/** The text of a failure: our own words for a model problem, else what the core said. */
function noticeText(notice: Notice, t: ReturnType<typeof useT>): string {
  switch (notice.reason) {
    case "offline":
      return t("chat.error.offline");
    case "auth":
      return t("chat.error.auth");
    case "quota":
      return t("chat.error.quota");
    case "model_missing":
      return t("chat.error.modelMissing");
    default:
      return notice.message;
  }
}

const PROVIDER_NAMES: Record<string, string> = { anthropic: "Anthropic", openai: "OpenAI" };

/** "Qwen3-4B · local", "claude-sonnet-5-5 · Anthropic", plus "· thought for 18 s" when it thought. */
function metaOf(
  message: Message,
  thought: Thought | undefined,
  t: ReturnType<typeof useT>,
): string | null {
  if (!message.model) return null;
  const parts = [message.model];
  if (message.provider) {
    parts.push(
      message.provider === "local"
        ? t("chat.providerLocal")
        : (PROVIDER_NAMES[message.provider] ?? message.provider),
    );
  }
  if (thought) parts.push(t("chat.thoughtMeta", { seconds: thoughtSeconds(thought) }));
  return parts.join(" · ");
}

type AnswerProps = {
  text: string;
  sources: Source[];
  thinking?: ReactNode;
  meta: string | null;
  hint: boolean;
  collectionName: string;
  done?: boolean;
  active: number | null;
  onOpen: (source: Source) => void;
};

/**
 * An assistant answer: the thinking block, text with citation chips, source cards, model, and the
 * hint. A reply that has sources but no text is a search-only result: the passages are the reply.
 */
function Answer({
  text,
  sources,
  thinking,
  meta,
  hint,
  collectionName,
  done = true,
  active,
  onOpen,
}: AnswerProps) {
  const t = useT();
  if (done && isNotFound(text, sources.length)) {
    return (
      <div className="message assistant">
        <div className="not-found">
          <SearchX size={18} />
          <div>
            <h3>{t("chat.notFoundTitle", { name: collectionName })}</h3>
            <p>{t("chat.notFoundText")}</p>
          </div>
        </div>
        {meta && <p className="answer-meta">{meta}</p>}
      </div>
    );
  }
  if (!text && sources.length > 0) {
    return (
      <div className="message assistant">
        <h3 className="passages-label">{t("chat.passages")}</h3>
        <ul className="passages">
          {sources.map((source) => (
            <li key={source.n}>
              <button type="button" className="passage" onClick={() => onOpen(source)}>
                <span className="passage-head">
                  <FileText size={16} />
                  <span className="passage-file" title={source.file}>
                    {fileName(source.file)}
                  </span>
                  <span className="passage-label">{sourceLabel(source)}</span>
                </span>
                <span className="passage-text">{source.text}</span>
              </button>
            </li>
          ))}
        </ul>
      </div>
    );
  }
  return (
    <div className="message assistant">
      {thinking}
      <AnswerText
        text={text}
        sourceCount={sources.length}
        active={active}
        onCite={(n) => {
          const source = sourceOf(sources, n);
          if (source) onOpen(source);
        }}
      />
      {sources.length > 0 && (
        <ul className="source-cards">
          {sources.map((source) => (
            <li key={source.n}>
              <button
                type="button"
                className="source-card"
                onClick={() => onOpen(source)}
              >
                <span className="source-num">{source.n}</span>
                <span className="source-meta">
                  <span className="source-file" title={source.file}>
                    {fileName(source.file)}
                  </span>
                  {(source.label || folderOf(source.file)) && (
                    <span className="source-label" title={source.file}>
                      {sourceLabel(source)}
                    </span>
                  )}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {meta && <p className="answer-meta">{meta}</p>}
      {hint && sources.length > 0 && (
        <p className="answer-hint">{t("chat.citeHint")}</p>
      )}
    </div>
  );
}

/** Where a source is: its page or section, then its folder ("p. 2 · Contracts/2026"). */
function sourceLabel(source: Source): string {
  return [source.label, folderOf(source.file)].filter(Boolean).join(" · ");
}

/** The file name of a collection-relative path ("a/b/c.pdf" -> "c.pdf"). */
export function fileName(path: string): string {
  return path.slice(path.lastIndexOf("/") + 1);
}

/** The folder part of a collection-relative path ("a/b/c.pdf" -> "a/b"; "" at the top). */
export function folderOf(path: string): string {
  const cut = path.lastIndexOf("/");
  return cut < 0 ? "" : path.slice(0, cut);
}

function SourcePanel({ source, onClose }: { source: Source; onClose: () => void }) {
  const t = useT();
  return (
    <aside className="source-panel" aria-label={t("chat.sourcePanel")}>
      <header>
        <FileText size={20} />
        <div className="source-title">
          <strong title={source.file}>{fileName(source.file)}</strong>
          <span>{sourceLabel(source)}</span>
        </div>
        <button type="button" className="icon-button" aria-label={t("chat.closeSource")} onClick={onClose}>
          <X size={18} />
        </button>
      </header>
      <div className="source-body">
        <div className="paper">
          <TextWithParagraphs text={source.text} />
        </div>
      </div>
    </aside>
  );
}
