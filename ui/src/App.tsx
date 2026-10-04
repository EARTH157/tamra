import { AlertTriangle, BookOpen, Plus, RefreshCw, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import ChatList from "./ChatList";
import ChatView from "./ChatView";
import DeleteChatDialog from "./DeleteChatDialog";
import { useT } from "./i18n";
import IndexStatus from "./IndexStatus";
import Setup from "./Setup";
import type { Chat, CollectionState, SettingsTab } from "./types";
import Welcome from "./Welcome";

const POLL_MS = 2000;

export default function App() {
  const t = useT();
  const [state, setState] = useState<CollectionState | null>(null);
  const [offline, setOffline] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [chats, setChats] = useState<Chat[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [choosingFolder, setChoosingFolder] = useState(false);
  const [deleting, setDeleting] = useState<Chat | null>(null);

  const wasOffline = useRef(false);

  const refreshChats = useCallback(async () => {
    try {
      setChats((await api<{ chats: Chat[] }>("GET", "/api/chats")).chats);
    } catch (e) {
      setProblem((e as Error).message);
    }
  }, []);

  const refreshCollection = useCallback(async () => {
    try {
      setState(await api<CollectionState>("GET", "/api/collection"));
      setOffline(null);
      if (wasOffline.current) {
        wasOffline.current = false;
        void refreshChats(); // the core may have restarted: reload what it has
      }
    } catch (e) {
      wasOffline.current = true;
      setOffline((e as Error).message);
    }
  }, [refreshChats]);

  useEffect(() => {
    void refreshCollection();
    void refreshChats();
    const timer = window.setInterval(() => void refreshCollection(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [refreshCollection, refreshChats]);

  async function retry() {
    await refreshCollection();
  }

  async function createChat(): Promise<number> {
    const chat = await api<Chat>("POST", "/api/chats");
    setChats((list) => [chat, ...list]);
    setActiveId(chat.id);
    return chat.id;
  }

  async function renameChat(id: number, title: string) {
    try {
      const chat = await api<Chat>("PATCH", `/api/chats/${id}`, { title });
      setChats((list) => list.map((item) => (item.id === id ? chat : item)));
    } catch (e) {
      setProblem((e as Error).message);
    }
  }

  async function deleteChat(id: number) {
    try {
      await api("DELETE", `/api/chats/${id}`);
      setChats((list) => list.filter((chat) => chat.id !== id));
      setActiveId((active) => (active === id ? null : active));
    } catch (e) {
      setProblem((e as Error).message);
    } finally {
      setDeleting(null);
    }
  }

  async function rebuild() {
    try {
      await api("POST", "/api/collection/rebuild");
      await refreshCollection();
    } catch (e) {
      setProblem((e as Error).message);
    }
  }

  /** Placeholder: the Settings page (Task 9) replaces this with a switch to that page and tab. */
  function openSettings(tab: SettingsTab) {
    void tab;
  }

  const collection = state?.collection ?? null;
  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <span className="logo-tile">
            <BookOpen size={17} />
          </span>
          <span className="brand-name">Tamra</span>
          <span className="brand-thai" lang="th">
            ตำรา
          </span>
        </div>
        <button
          type="button"
          className="new-chat"
          onClick={() => {
            setProblem(null);
            setActiveId(null);
          }}
          disabled={busy || collection === null}
        >
          <Plus size={16} />
          {t("app.newChat")}
        </button>
        <div className="section-label">{t("app.chats")}</div>
        {state !== null && collection === null ? (
          <p className="sidebar-note">{t("app.noChatsNoFolder")}</p>
        ) : state !== null && chats.length === 0 ? (
          <p className="sidebar-note">{t("app.noChatsYet")}</p>
        ) : (
          <ChatList
            chats={chats}
            activeId={activeId}
            disabled={busy}
            onSelect={(id) => {
              setProblem(null);
              setActiveId(id);
            }}
            onRename={renameChat}
            onDelete={setDeleting}
          />
        )}
        <div className="sidebar-fill" />
        {collection && (
          <IndexStatus
            collection={collection}
            index={state?.index ?? null}
            disabled={busy}
            onChangeFolder={() => setChoosingFolder(true)}
            onRebuild={() => void rebuild()}
          />
        )}
      </aside>
      <main className="main">
        {offline ? (
          <div className="banner" role="alert">
            <AlertTriangle size={16} />
            <span className="banner-text" title={offline}>
              {t("app.coreUnreachable")}
            </span>
            <button type="button" className="retry" onClick={() => void retry()}>
              <RefreshCw size={14} />
              {t("common.retry")}
            </button>
          </div>
        ) : (
          problem && (
            <div className="banner" role="alert">
              <AlertTriangle size={16} />
              <span className="banner-text">{problem}</span>
              <button
                type="button"
                className="icon-button"
                aria-label={t("common.dismiss")}
                onClick={() => setProblem(null)}
              >
                <X size={16} />
              </button>
            </div>
          )
        )}
        {state === null ? (
          <div className="connecting">{offline ? null : t("app.connecting")}</div>
        ) : collection === null ? (
          <Welcome onChoose={() => setChoosingFolder(true)} />
        ) : (
          <ChatView
            chatId={activeId}
            collectionName={collection.name}
            createChat={createChat}
            onBusyChange={setBusy}
            onAnswered={() => void refreshChats()}
            onOpenSettings={openSettings}
          />
        )}
      </main>
      {choosingFolder && (
        <Setup
          title={collection ? t("folder.changeTitle") : t("folder.chooseTitle")}
          description={collection ? t("folder.changeText") : t("folder.chooseText")}
          initialFolder={collection?.folder_path ?? ""}
          onDone={(next) => {
            setState(next);
            setChoosingFolder(false);
          }}
          onCancel={() => setChoosingFolder(false)}
        />
      )}
      {deleting && (
        <DeleteChatDialog
          chat={deleting}
          onConfirm={() => deleteChat(deleting.id)}
          onCancel={() => setDeleting(null)}
        />
      )}
    </div>
  );
}
