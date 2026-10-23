import { Check as CheckIcon, Copy, FileText, Info, Lightbulb, MessageCircle, SearchX, X } from "lucide-react";
import { type CSSProperties, type ReactNode, useEffect, useRef, useState } from "react";
import AnswerText, { type Highlight } from "./AnswerText";
import { api, attribute, streamAnswer } from "./api";
import { attributionErrorText, errorText, projectErrorText } from "./apiErrors";
import CheckSourceButton from "./CheckSourceButton";
import { splitCitations } from "./citations";
import Composer from "./Composer";
import { type ContextUse, contextUse, savedContextUse } from "./ContextMeter";
import DocumentViewer from "./DocumentViewer";
import { useT } from "./i18n";
import logo from "./logo.svg";
import { canThink, useModels } from "./models";
import { isNotFound } from "./notFound";
import { utf16ToCp } from "./offsets";
import { MIN_SELECTION, type Selected, sentenceBefore } from "./selection";
import { useSettings } from "./settings";
import Splitter from "./Splitter";
import SourcePanel, {
  type Check,
  fileName,
  folderOf,
  shownFile,
  sourceLabel,
  spansProjects,
} from "./SourcePanel";
import Thinking, { type Thought, thoughtSeconds } from "./Thinking";
import type {
  AnswerMode,
  Chat,
  ChatDetail,
  ErrorReason,
  Message,
  Project,
  SettingsChanges,
  SettingsTab,
  Source,
  ViewerRequest,
} from "./types";

/** A message for the user, with the reason the model call failed when the core gave one. */
type Notice = { message: string; reason?: ErrorReason };

/** The local model's generated tokens and speed for one answer. */
type Generated = { count: number; rate: number };

type Pending = {
  question: string;
  mode: AnswerMode;
  sources: Source[];
  text: string;
  thought: Thought | null;
  generated: Generated | null;
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
  /** Create the chat for its first question; `general` for a Chat-mode one (no home project). */
  createChat: (general: boolean) => Promise<number>;
  onBusyChange: (busy: boolean) => void;
  onAnswered: () => void;
  /** Open Settings on a tab: "Manage models…" and the "no model" error lead to "model". */
  onOpenSettings: (tab: SettingsTab) => void;
  /** False while Settings covers the chat; the models are reloaded when it shows again. */
  visible?: boolean;
  /** The projects the chat can search: the "Search in" chip is shown when there are any. */
  projects?: Project[];
  /** The open chat as the app lists it; it is current after a move or a deleted project. */
  chat?: Chat | null;
  /** The home project of a chat that does not exist yet: it is searched unless another is chosen. */
  defaultProjectId?: number | null;
  /** The search scope was saved: the app's list of chats takes the updated chat. */
  onChatChanged?: (chat: Chat) => void;
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

/** The source panel's width at start, and how narrow it can be dragged, in CSS pixels. */
export const PANEL_WIDTH = 520;
const PANEL_MIN = 320;
/** How narrow the chat can get as the source panel is dragged wider; the panel's CSS says it too. */
const CHAT_MIN = 400;

/** One chat: its messages, the answer being streamed, the question box, and a source panel. */
export default function ChatView({
  chatId,
  collectionName,
  createChat,
  onBusyChange,
  onAnswered,
  onOpenSettings,
  visible = true,
  projects,
  chat,
  defaultProjectId = null,
  onChatChanged,
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
  // Likewise the local model's token counts of those answers, by message id.
  const [generated, setGenerated] = useState<Record<number, Generated>>({});
  // How full each chat's last prompt made the context window, by chat id: saved with the chat,
  // then kept live while it is answered.
  const [contexts, setContexts] = useState<Record<number, ContextUse>>({});
  const [notice, setNotice] = useState<Notice | null>(null);
  const [opened, setOpened] = useState<Opened | null>(null);
  const [viewer, setViewer] = useState<ViewerRequest | null>(null);
  // A selection being checked against the sources of its answer. It and `opened` are never both set.
  const [check, setCheck] = useState<Check | null>(null);
  const [panelWidth, setPanelWidth] = useState(PANEL_WIDTH);
  const view = useRef<HTMLDivElement>(null);
  const checkSeq = useRef(0); // a check that was replaced or closed is ignored when it answers
  // The CPU-fallback line is shown until dismissed, then not again while the app stays open.
  const [cpuNoticeDismissed, setCpuNoticeDismissed] = useState(false);
  // The projects whose index was left out of the last answer (it must be rebuilt first).
  const [skipped, setSkipped] = useState<string[]>([]);
  // A scope chosen but not yet confirmed by the core: for a chat that does not exist yet it is
  // kept until the chat is created; for an open chat it shows until the core answers.
  const [chosenScope, setChosenScope] = useState<number[] | null>(null);
  const scopeSeq = useRef(0); // only the newest scope change is applied when it answers
  const scopeSaves = useRef<Promise<unknown>>(Promise.resolve()); // saves still in flight
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
    checkSeq.current++;
    setCheck(null);
    setViewer(null);
    setNotice(null);
    setSkipped([]);
    scopeSeq.current++;
    setChosenScope(null);
    setThoughts({});
    setGenerated({});
    setDetail(null);
    if (chatId === null) return;
    const id = chatId;
    let current = true;
    api<ChatDetail>("GET", `/api/chats/${id}`)
      .then((loaded) => {
        if (!current) return;
        setDetail(loaded);
        const saved = loaded.context;
        // A live meter from this session is as new as the saved one, or newer.
        if (saved) setContexts((all) => (all[id] ? all : { ...all, [id]: savedContextUse(saved) }));
      })
      .catch((e: Error) => {
        if (current) setNotice({ message: projectErrorText(e, t) });
      });
    return () => {
      current = false;
    };
  }, [chatId]);

  // The default project changed while a new chat is being composed: a choice made for the old
  // default no longer applies.
  useEffect(() => {
    if (chatId === null) setChosenScope(null);
  }, [defaultProjectId, chatId]);

  // Keep the newest message in view.
  useEffect(() => {
    const el = scroller.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [detail, pending, notice]);

  /** Ask the question in the box, or `again` (the question of a not-found reply) in chat mode. */
  async function ask(again?: string) {
    const text = again ?? question.trim();
    const askMode: AnswerMode = again === undefined ? mode : "chat";
    if (!text || asking.current) return;
    asking.current = true;
    if (again === undefined) setQuestion(""); // a draft in the box stays when asking again
    setNotice(null);
    setSkipped([]);
    setOpened(null);
    checkSeq.current++;
    setCheck(null);
    let state: Pending = {
      question: text,
      mode: askMode,
      sources: [],
      text: "",
      thought: null,
      generated: null,
      error: null,
      done: false,
      messageId: null,
    };
    setPending(state);
    onBusyChange(true);
    let usedId: number | null = chatId;
    let streaming = false;
    try {
      await scopeSaves.current; // the question is asked over the scope the chip shows
      usedId = chatId ?? (await createChat(askMode === "chat"));
      // A new chat searches its home project; a different choice is saved before the question.
      if (chatId === null && chosenScope) {
        try {
          await saveScope(usedId, chosenScope);
        } finally {
          setChosenScope(null); // the chip shows what the core has, saved or not
        }
      } else if (askMode !== "chat" && chatId !== null && homeId === null && !known?.scope.length) {
        // A general chat has no home to fall back on: it searches what the chip shows.
        await saveScope(usedId, searched);
      }
      streaming = true;
      const id = usedId;
      await streamAnswer(
        usedId,
        text,
        (e) => {
          if (e.type === "sources") {
            state = { ...state, sources: e.sources };
          } else if (e.type === "notice") {
            setSkipped(e.names);
          } else if (e.type === "context") {
            setContexts((all) => ({ ...all, [id]: contextUse(e) }));
          } else if (e.type === "usage") {
            state = { ...state, generated: { count: e.generated_tokens, rate: e.tokens_per_second } };
            const measured = { prompt: e.prompt_tokens, generated: e.generated_tokens };
            setContexts((all) => (all[id] ? { ...all, [id]: { ...all[id], measured } } : all));
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
        { mode: askMode, think: think && thinkAvailable },
      );
    } catch (e) {
      // Before the stream (creating the chat or saving its scope) the core's refusals are known.
      const message = streaming ? (e as Error).message : projectErrorText(e as Error, t);
      state = { ...state, error: { message } };
      if (!streaming) setQuestion((current) => current || text); // nothing was asked: keep the text
    } finally {
      const saved = usedId !== null ? await reload(usedId) : null;
      asking.current = false;
      // The core knows whether the GPU is used only once llama-server has started: after an answer.
      if (settings.mode === "local") void refreshModels();
      const thought = endThought(state.thought);
      const savedId = state.messageId;
      if (thought && savedId !== null) setThoughts((all) => ({ ...all, [savedId]: thought }));
      const counts = state.generated;
      if (counts && savedId !== null) setGenerated((all) => ({ ...all, [savedId]: counts }));
      setNotice(state.error);
      // The core saves nothing for this question: give the text back so it can be asked again.
      if (state.error?.reason === "embedding_missing") setQuestion((current) => current || text);
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
          current ?? {
            id,
            title: "",
            created_at: "",
            updated_at: "",
            project_id: null,
            scope: [],
            messages: [],
            context: null,
          },
      );
      return null;
    }
  }

  /** Save the projects a chat searches; the open chat and the app take what the core answers. */
  async function saveScope(id: number, ids: number[], mine?: number): Promise<void> {
    const updated = await api<Chat>("PUT", `/api/chats/${id}/scope`, { project_ids: ids });
    if (mine !== undefined && mine !== scopeSeq.current) return; // a newer choice is on its way
    setDetail((current) => (current && current.id === updated.id ? { ...current, ...updated } : current));
    onChatChanged?.(updated);
  }

  /** The "Search in" chip: save the choice for an open chat, or keep it until the chat exists. */
  function changeScope(ids: number[]) {
    setChosenScope(ids);
    if (chatId === null) return;
    const mine = ++scopeSeq.current;
    const saving = saveScope(chatId, ids, mine)
      .then(() => mine === scopeSeq.current && setChosenScope(null))
      .catch((e: Error) => {
        if (mine !== scopeSeq.current) return;
        setChosenScope(null);
        setNotice({ message: projectErrorText(e, t) });
      });
    scopeSaves.current = Promise.all([scopeSaves.current, saving]);
  }

  function closePanel() {
    checkSeq.current++;
    setOpened(null);
    setCheck(null);
  }

  /** Find where a selected part of a saved answer comes from; the panel shows the progress. */
  function startCheck(messageId: number, selected: Selected, n: number | null) {
    const id = ++checkSeq.current;
    setOpened(null);
    setCheck({
      id,
      messageId,
      selection: selected.text,
      start: selected.start,
      end: selected.end,
      n,
      status: "loading",
      matches: [],
      index: 0,
      error: null,
    });
    // The core counts characters as Python does; the selection's offset is a UTF-16 index.
    const content = detail?.messages.find((m) => m.id === messageId)?.content;
    const start = content === undefined ? undefined : utf16ToCp(content, selected.start);
    attribute(messageId, selected.text, n, start)
      .then((result) =>
        setCheck((c) => (c?.id === id ? { ...c, status: "ready", matches: result.matches } : c)),
      )
      .catch((e: Error) =>
        setCheck((c) => (c?.id === id ? { ...c, status: "error", error: attributionErrorText(e, t) } : c)),
      );
  }

  /** A source card or passage: show that snapshot as it is. */
  function openSource(owner: number | "pending", source: Source) {
    checkSeq.current++;
    setCheck(null);
    setOpened({ owner, source });
  }

  // The "Open file" button of the panel: the document viewer opens over the chat.
  function openViewer(request: ViewerRequest) {
    setViewer(request);
  }

  function stop() {
    api("POST", "/api/answer/cancel").catch((e: Error) => setNotice({ message: e.message }));
  }

  /** Save the model choice. The menu follows the settings; a refusal is shown as a notice. */
  function chooseModel(changes: SettingsChanges) {
    update(changes).catch((e: Error) => setNotice({ message: e.message }));
  }

  /** Back to the local model after a cloud failure; the failure notice goes away. */
  function switchToLocal() {
    update({ mode: "local" })
      .then(() => setNotice(null))
      .catch((e: Error) => setNotice({ message: errorText(e, t) }));
  }

  // A cloud call that failed for a reason the local model does not share: offer it instead.
  const canFallBack =
    settings.mode === "api" &&
    models?.active.id != null &&
    (notice?.reason === "offline" || notice?.reason === "auth" || notice?.reason === "quota");
  const cpuNotice =
    settings.mode === "local" && models?.gpu_offload === false && !cpuNoticeDismissed;

  // The projects searched: what was chosen and not yet confirmed, else the open chat's scope (from
  // the app's list, which a move or a deleted project keeps current), else its home project.
  const known = chat && chat.id === chatId ? chat : detail;
  // A general chat has no home project.
  const homeId = chatId === null || !known ? defaultProjectId : known.project_id;
  const inProjects = (ids: number[]) => ids.filter((id) => projects?.some((p) => p.id === id));
  const searched = [chosenScope ?? known?.scope ?? [], homeId === null ? [] : [homeId]]
    .map(inProjects)
    .find((ids) => ids.length > 0) ?? inProjects(projects?.[0] ? [projects[0].id] : []);

  const messages: Message[] = detail?.messages ?? [];
  const ready = chatId === null || detail !== null;
  const empty = ready && messages.length === 0 && !pending && !notice;
  const lastAnswer = [...messages].reverse().find((message) => message.role === "assistant");
  // The question a not-found reply answered: the last one can be asked again in chat mode.
  const lastIndex = lastAnswer ? messages.indexOf(lastAnswer) : -1;
  const lastQuestion = messages.slice(0, lastIndex).reverse().find((m) => m.role === "user");
  const askAgain =
    lastAnswer && lastQuestion && !pending && isNotFound(lastAnswer.content, lastAnswer.sources.length)
      ? () => void ask(lastQuestion.content)
      : undefined;
  // The chips of the source being shown are drawn filled.
  const activeFor = (owner: number | "pending") => {
    if (check?.messageId === owner) {
      return check.status === "ready" ? (check.matches[check.index]?.n ?? null) : check.n;
    }
    return opened?.owner === owner ? opened.source.n : null;
  };
  // The span being checked stays marked in its answer while the panel is open.
  const highlightFor = (owner: number): Highlight | null =>
    check?.messageId === owner ? { start: check.start, end: check.end } : null;
  const panelOwner = check?.messageId ?? opened?.owner ?? null;
  const panelMessage = messages.find((message) => message.id === panelOwner);

  return (
    <div className="chat-view" ref={view} style={{ "--panel-width": `${panelWidth}px` } as CSSProperties}>
      <div className="chat-column">
        <div className="messages" ref={scroller}>
          {empty && (
            <div className="hero">
              <img className="hero-tile" src={logo} alt="" />
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
                messageId={message.id}
                text={message.content}
                sources={message.sources}
                thinking={
                  thoughts[message.id] && <Thinking thought={thoughts[message.id]} />
                }
                meta={metaOf(message, thoughts[message.id], generated[message.id], t)}
                hint={message === lastAnswer}
                general={isGeneral(message.content, message.sources)}
                collectionName={collection}
                active={activeFor(message.id)}
                highlight={highlightFor(message.id)}
                onOpen={(source) => openSource(message.id, source)}
                onCheck={(selected, n) => startCheck(message.id, selected, n)}
                onAskAnyway={message === lastAnswer ? askAgain : undefined}
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
                  general={pending.mode === "chat"}
                  collectionName={collection}
                  done={pending.done}
                  active={activeFor("pending")}
                  onOpen={(source) => openSource("pending", source)}
                />
              ) : (
                !pending.error && (
                  <div className="message assistant">
                    {pending.thought ? (
                      <Thinking thought={pending.thought} />
                    ) : (
                      <p className="searching">
                        <Lightbulb size={16} />
                        {pending.mode === "chat" ? t("chat.writing") : t("chat.searching")}
                      </p>
                    )}
                  </div>
                )
              )}
              {pending.generated && (
                <p className="answer-meta live-tokens">
                  {t("chat.tokens", {
                    count: pending.generated.count,
                    rate: pending.generated.rate,
                  })}
                </p>
              )}
            </>
          )}
          {notice && (
            <div className="chat-error" role="alert">
              <span>{noticeText(notice, t)}</span>
              {(notice.reason === "model_missing" ||
                notice.reason === "model_unsupported" ||
                notice.reason === "embedding_missing") && (
                <button type="button" className="btn" onClick={() => onOpenSettings("model")}>
                  {t("chat.openModelSettings")}
                </button>
              )}
              {canFallBack && (
                <button type="button" className="btn" onClick={switchToLocal}>
                  {t("chat.useLocal")}
                </button>
              )}
            </div>
          )}
        </div>
        {skipped.length > 0 && (
          <div className="chat-notice" role="status">
            <Info size={16} />
            <span>{t("chat.staleSkipped", { names: skipped.join(", ") })}</span>
            <button
              type="button"
              className="icon-button"
              aria-label={t("common.dismiss")}
              onClick={() => setSkipped([])}
            >
              <X size={16} />
            </button>
          </div>
        )}
        {cpuNotice && (
          <div className="chat-notice" role="status">
            <Info size={16} />
            <span>{t("model.cpuFallback")}</span>
            <button
              type="button"
              className="icon-button"
              aria-label={t("common.dismiss")}
              onClick={() => setCpuNoticeDismissed(true)}
            >
              <X size={16} />
            </button>
          </div>
        )}
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
          scope={
            // a chat-mode question searches nothing, so there is no scope to choose
            projects && searched.length > 0 && mode !== "chat"
              ? { projects, selected: searched, homeId, onChange: changeScope }
              : undefined
          }
          context={chatId !== null ? contexts[chatId] : undefined}
        />
      </div>
      {(check || opened) && (
        <Splitter
          label={t("chat.resizeSource")}
          orientation="vertical"
          size={panelWidth}
          min={PANEL_MIN}
          max={() => (view.current?.clientWidth ?? 1000) - CHAT_MIN}
          grow={-1}
          onResize={setPanelWidth}
        />
      )}
      {(check || opened) && (
        <SourcePanel
          sources={panelMessage?.sources ?? []}
          multiProject={spansProjects(
            panelMessage?.sources ?? (opened?.owner === "pending" ? (pending?.sources ?? []) : []),
          )}
          messageId={typeof panelOwner === "number" ? panelOwner : null}
          check={check}
          source={opened?.source ?? null}
          onIndexChange={(index) => setCheck((c) => (c ? { ...c, index } : c))}
          onOpenSource={(source) => panelOwner !== null && openSource(panelOwner, source)}
          onOpenViewer={openViewer}
          onClose={closePanel}
        />
      )}
      {viewer && (
        <DocumentViewer
          request={viewer}
          sources={messages.find((message) => message.id === viewer.messageId)?.sources ?? []}
          onClose={() => setViewer(null)}
        />
      )}
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
    case "model_unsupported":
      return t("chat.error.modelUnsupported");
    case "embedding_missing":
      return t("error.embeddingMissing");
    default:
      return notice.message;
  }
}

const PROVIDER_NAMES: Record<string, string> = { anthropic: "Anthropic", openai: "OpenAI" };

/** A saved chat-mode reply: model text with no sources that is not the not-found reply. */
function isGeneral(content: string, sources: Source[]): boolean {
  return content !== "" && sources.length === 0 && !isNotFound(content, 0);
}

/**
 * "Qwen3-4B · local", "claude-sonnet-5-5 · Anthropic", plus "· thought for 18 s" when it thought
 * and "· 460 tokens · 21 tokens/s" when the local model counted them in this session.
 */
function metaOf(
  message: Message,
  thought: Thought | undefined,
  counts: Generated | undefined,
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
  if (counts) parts.push(t("chat.tokens", { count: counts.count, rate: counts.rate }));
  return parts.join(" · ");
}

/**
 * An answer as plain text to paste elsewhere: its text with the [n] markers, then the sources it
 * cites, each with its path in the project and its place ("[1] 2026/lease.pdf, p. 2"). Uncited
 * sources are left out.
 */
function copyableAnswer(text: string, sources: Source[], multi: boolean, heading: string): string {
  const cited = new Set(
    splitCitations(text, sources.length).flatMap((segment) => (segment.kind === "cite" ? [segment.n] : [])),
  );
  const lines = sources
    .filter((source) => cited.has(source.n))
    .map((source) => {
      const file = multi && source.project ? `${source.project} · ${source.file}` : source.file;
      return `[${source.n}] ${[file, source.label].filter(Boolean).join(", ")}`;
    });
  return lines.length ? `${text.trim()}\n\n${heading}\n${lines.join("\n")}` : text.trim();
}

/** Copy an answer to the clipboard, and say for a moment that it did (or could not). */
function CopyButton({ text }: { text: () => string }) {
  const t = useT();
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  useEffect(() => {
    if (state === "idle") return;
    const timer = window.setTimeout(() => setState("idle"), 2000);
    return () => window.clearTimeout(timer);
  }, [state]);
  return (
    <button
      type="button"
      className="link-button copy-answer"
      onClick={() =>
        navigator.clipboard.writeText(text()).then(
          () => setState("copied"),
          () => setState("failed"), // some web views refuse clipboard writes
        )
      }
    >
      {state === "copied" ? <CheckIcon size={13} /> : <Copy size={13} />}
      {state === "copied" ? t("chat.copied") : state === "failed" ? t("chat.copyFailed") : t("chat.copy")}
    </button>
  );
}

type AnswerProps = {
  /** The saved message's id; absent while the answer is being streamed (it cannot be checked yet). */
  messageId?: number;
  text: string;
  sources: Source[];
  thinking?: ReactNode;
  meta: string | null;
  hint: boolean;
  /** A chat-mode reply: it says it is not from the documents, and has nothing to check. */
  general: boolean;
  collectionName: string;
  done?: boolean;
  active: number | null;
  highlight?: Highlight | null;
  /** Open a source as it is, without comparing it to anything. */
  onOpen: (source: Source) => void;
  /** Check a part of the saved answer: a selection, or the sentence of a clicked chip (with `n`). */
  onCheck?: (selected: Selected, n: number | null) => void;
  /** Shown on a not-found reply: ask its question again in chat mode. */
  onAskAnyway?: () => void;
};

/**
 * An assistant answer: the thinking block, text with citation chips, source cards, model, and the
 * hint. A reply that has sources but no text is a search-only result: the passages are the reply.
 * A general (chat-mode) reply has text and no sources.
 */
function Answer({
  messageId,
  text,
  sources,
  thinking,
  meta,
  hint,
  general,
  collectionName,
  done = true,
  active,
  highlight = null,
  onOpen,
  onCheck,
  onAskAnyway,
}: AnswerProps) {
  const t = useT();
  const textRoot = useRef<HTMLDivElement>(null);
  // The project is named on a source only when the answer drew on more than one.
  const multi = spansProjects(sources);
  if (done && isNotFound(text, sources.length)) {
    return (
      <div className="message assistant">
        <div className="not-found">
          <SearchX size={18} />
          <div>
            <h3>{t("chat.notFoundTitle", { name: collectionName })}</h3>
            <p>{t("chat.notFoundText")}</p>
            {onAskAnyway && (
              <button type="button" className="btn not-found-action" onClick={onAskAnyway}>
                <MessageCircle size={14} />
                {t("chat.askAnyway")}
              </button>
            )}
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
                    {shownFile(source, multi)}
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
      {general && (
        <p className="answer-general">
          <Info size={14} />
          {t("chat.general")}
        </p>
      )}
      <AnswerText
        ref={textRoot}
        text={text}
        sourceCount={sources.length}
        active={active}
        highlight={highlight}
        onCite={(n, index) => {
          const source = sourceOf(sources, n);
          if (!source) return;
          if (messageId === undefined || !onCheck) return onOpen(source);
          // A chip checks the sentence it ends, against its own source.
          const { start, end } = sentenceBefore(text, index);
          if (end - start < MIN_SELECTION) return onOpen(source); // too little before it to check
          onCheck({ text: text.slice(start, end), start, end }, n);
        }}
      />
      {messageId !== undefined && onCheck && !general && (
        <CheckSourceButton rootRef={textRoot} content={text} onCheck={(selected) => onCheck(selected, null)} />
      )}
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
                    {shownFile(source, multi)}
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
      {(meta || messageId !== undefined) && (
        <div className="answer-foot">
          {meta && <p className="answer-meta">{meta}</p>}
          {messageId !== undefined && (
            <CopyButton text={() => copyableAnswer(text, sources, multi, t("chat.copySources"))} />
          )}
        </div>
      )}
      {hint && sources.length > 0 && (
        <p className="answer-hint">{t("chat.citeHint")}</p>
      )}
    </div>
  );
}
