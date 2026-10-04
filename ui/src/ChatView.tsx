import { ArrowUp, BookOpen, FileText, Lightbulb, SearchX, Square, X } from "lucide-react";
import {
  type FormEvent,
  type KeyboardEvent,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import AnswerText, { TextWithParagraphs } from "./AnswerText";
import { api, streamAnswer } from "./api";
import { useT } from "./i18n";
import { isNotFound } from "./notFound";
import type { ChatDetail, Message, Source } from "./types";

type Pending = {
  question: string;
  sources: Source[];
  text: string;
  error: string | null;
  done: boolean;
};

/** The open source and the answer it belongs to ("pending" for the one being streamed). */
type Opened = { owner: number | "pending"; source: Source };

type Props = {
  chatId: number | null;
  collectionName?: string;
  createChat: () => Promise<number>;
  onBusyChange: (busy: boolean) => void;
  onAnswered: () => void;
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
}: Props) {
  const t = useT();
  const collection = collectionName ?? t("chat.defaultCollection");
  const [detail, setDetail] = useState<ChatDetail | null>(null);
  const [question, setQuestion] = useState("");
  const [pending, setPending] = useState<Pending | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [opened, setOpened] = useState<Opened | null>(null);
  const asking = useRef(false);
  const scroller = useRef<HTMLDivElement>(null);
  const box = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (asking.current) return; // this chat was just created for the question being answered
    setOpened(null);
    setNotice(null);
    setDetail(null);
    if (chatId === null) return;
    let current = true;
    api<ChatDetail>("GET", `/api/chats/${chatId}`)
      .then((loaded) => {
        if (current) setDetail(loaded);
      })
      .catch((e: Error) => {
        if (current) setNotice(e.message);
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

  // Grow the question box with its text, up to the CSS max-height.
  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [question]);

  async function ask(event?: FormEvent) {
    event?.preventDefault();
    const text = question.trim();
    if (!text || asking.current) return;
    asking.current = true;
    setQuestion("");
    setNotice(null);
    setOpened(null);
    let state: Pending = { question: text, sources: [], text: "", error: null, done: false };
    setPending(state);
    onBusyChange(true);
    let usedId: number | null = chatId;
    try {
      usedId = chatId ?? (await createChat());
      await streamAnswer(usedId, text, (e) => {
        if (e.type === "sources") state = { ...state, sources: e.sources };
        else if (e.type === "token") state = { ...state, text: state.text + e.text };
        else if (e.type === "error") state = { ...state, error: e.message };
        else if (e.type === "done") state = { ...state, done: true };
        setPending(state);
      });
    } catch (e) {
      state = { ...state, error: (e as Error).message };
    } finally {
      const saved = usedId !== null ? await reload(usedId) : null;
      asking.current = false;
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
    api("POST", "/api/answer/cancel").catch((e: Error) => setNotice(e.message));
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void ask();
    }
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
                meta={metaOf(message)}
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
              {pending.text || pending.done ? (
                <Answer
                  text={pending.text}
                  sources={pending.sources}
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
                    <p className="searching">
                      <Lightbulb size={16} />
                      {t("chat.searching")}
                    </p>
                  </div>
                )
              )}
            </>
          )}
          {notice && (
            <p className="chat-error" role="alert">
              {notice}
            </p>
          )}
        </div>
        <form className={empty ? "composer narrow" : "composer"} onSubmit={ask}>
          <div className="composer-card">
            <textarea
              ref={box}
              aria-label={t("chat.questionLabel")}
              placeholder={t("chat.placeholder")}
              rows={1}
              value={question}
              maxLength={4000}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={onKeyDown}
              disabled={!ready}
            />
            <div className="composer-actions">
              {pending ? (
                <button type="button" className="send" aria-label={t("chat.stop")} onClick={stop}>
                  <Square size={12} fill="currentColor" />
                </button>
              ) : (
                <button
                  type="submit"
                  className="send"
                  aria-label={t("chat.send")}
                  disabled={!ready || !question.trim()}
                >
                  <ArrowUp size={18} />
                </button>
              )}
            </div>
          </div>
          <p className="composer-hint">{t("chat.composerHint")}</p>
        </form>
      </div>
      {opened && <SourcePanel source={opened.source} onClose={() => setOpened(null)} />}
    </div>
  );
}

function metaOf(message: Message): string | null {
  if (!message.model) return null;
  return message.provider ? `${message.model} · ${message.provider}` : message.model;
}

type AnswerProps = {
  text: string;
  sources: Source[];
  meta: string | null;
  hint: boolean;
  collectionName: string;
  done?: boolean;
  active: number | null;
  onOpen: (source: Source) => void;
};

/** An assistant answer: text with citation chips, source cards, model, and the hint. */
function Answer({
  text,
  sources,
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
  return (
    <div className="message assistant">
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
                      {[source.label, folderOf(source.file)].filter(Boolean).join(" · ")}
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
          <span>{[source.label, folderOf(source.file)].filter(Boolean).join(" · ")}</span>
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
