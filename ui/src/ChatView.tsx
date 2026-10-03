import { type FormEvent, type KeyboardEvent, useEffect, useRef, useState } from "react";
import AnswerText from "./AnswerText";
import { api, streamAnswer } from "./api";
import type { ChatDetail, Message, Source } from "./types";

type Pending = { question: string; sources: Source[]; text: string; error: string | null };

type Props = {
  chatId: number | null;
  createChat: () => Promise<number>;
  onBusyChange: (busy: boolean) => void;
  onAnswered: () => void;
};

function sourceOf(sources: Source[], n: number): Source | null {
  return sources.find((source) => source.n === n) ?? null;
}

/** One chat: its messages, the answer being streamed, the question box, and a source panel. */
export default function ChatView({ chatId, createChat, onBusyChange, onAnswered }: Props) {
  const [detail, setDetail] = useState<ChatDetail | null>(null);
  const [question, setQuestion] = useState("");
  const [pending, setPending] = useState<Pending | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [opened, setOpened] = useState<Source | null>(null);
  const asking = useRef(false);

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

  async function ask(event?: FormEvent) {
    event?.preventDefault();
    const text = question.trim();
    if (!text || asking.current) return;
    asking.current = true;
    setQuestion("");
    setNotice(null);
    setOpened(null);
    let state: Pending = { question: text, sources: [], text: "", error: null };
    setPending(state);
    onBusyChange(true);
    try {
      const id = chatId ?? (await createChat());
      await streamAnswer(id, text, (e) => {
        if (e.type === "sources") state = { ...state, sources: e.sources };
        else if (e.type === "token") state = { ...state, text: state.text + e.text };
        else if (e.type === "error") state = { ...state, error: e.message };
        setPending(state);
      });
      setDetail(await api<ChatDetail>("GET", `/api/chats/${id}`));
    } catch (e) {
      state = { ...state, error: (e as Error).message };
    } finally {
      asking.current = false;
      setNotice(state.error);
      setPending(null);
      onBusyChange(false);
      onAnswered();
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
  return (
    <div className="chat-view">
      <div className="messages">
        {ready && messages.length === 0 && !pending && (
          <p className="muted empty">
            Ask a question about your documents. Answers cite their sources, like [1].
          </p>
        )}
        {messages.map((message) => (
          <MessageView key={message.id} message={message} onOpen={setOpened} />
        ))}
        {pending && (
          <>
            <div className="message user">{pending.question}</div>
            <div className="message assistant">
              <AnswerText
                text={pending.text || "…"}
                sourceCount={pending.sources.length}
                onCite={(n) => setOpened(sourceOf(pending.sources, n))}
              />
              <SourceList sources={pending.sources} onOpen={setOpened} />
            </div>
          </>
        )}
        {notice && (
          <p className="error" role="alert">
            {notice}
          </p>
        )}
      </div>
      {opened && <SourcePanel source={opened} onClose={() => setOpened(null)} />}
      <form className="ask" onSubmit={ask}>
        <textarea
          aria-label="Question"
          placeholder="Ask about your documents (Enter to send, Shift+Enter for a new line)"
          value={question}
          maxLength={4000}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={onKeyDown}
          disabled={!ready}
        />
        {pending ? (
          <button type="button" onClick={stop}>
            Stop
          </button>
        ) : (
          <button type="submit" disabled={!ready || !question.trim()}>
            Ask
          </button>
        )}
      </form>
    </div>
  );
}

function MessageView({ message, onOpen }: { message: Message; onOpen: (source: Source) => void }) {
  if (message.role === "user") return <div className="message user">{message.content}</div>;
  return (
    <div className="message assistant">
      <AnswerText
        text={message.content}
        sourceCount={message.sources.length}
        onCite={(n) => {
          const source = sourceOf(message.sources, n);
          if (source) onOpen(source);
        }}
      />
      <SourceList sources={message.sources} onOpen={onOpen} />
      {message.model && <p className="muted model">{message.model}</p>}
    </div>
  );
}

function SourceList({ sources, onOpen }: { sources: Source[]; onOpen: (source: Source) => void }) {
  if (sources.length === 0) return null;
  return (
    <ol className="source-list">
      {sources.map((source) => (
        <li key={source.n}>
          <button type="button" onClick={() => onOpen(source)}>
            [{source.n}] {source.file}
            {source.label ? ` · ${source.label}` : ""}
          </button>
        </li>
      ))}
    </ol>
  );
}

function SourcePanel({ source, onClose }: { source: Source; onClose: () => void }) {
  return (
    <aside className="source-panel" aria-label="Source">
      <header>
        <strong>
          [{source.n}] {source.file}
        </strong>
        <span className="muted">{source.label}</span>
        <button type="button" aria-label="Close source" onClick={onClose}>
          ×
        </button>
      </header>
      <p className="source-text">{source.text}</p>
    </aside>
  );
}
