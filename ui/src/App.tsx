import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import ChatList from "./ChatList";
import ChatView from "./ChatView";
import IndexStatus from "./IndexStatus";
import Setup from "./Setup";
import type { Chat, CollectionState } from "./types";

const POLL_MS = 2000;

export default function App() {
  const [state, setState] = useState<CollectionState | null>(null);
  const [offline, setOffline] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [chats, setChats] = useState<Chat[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [changingFolder, setChangingFolder] = useState(false);

  const refreshCollection = useCallback(async () => {
    try {
      setState(await api<CollectionState>("GET", "/api/collection"));
      setOffline(null);
    } catch (e) {
      setOffline((e as Error).message);
    }
  }, []);

  const refreshChats = useCallback(async () => {
    try {
      setChats((await api<{ chats: Chat[] }>("GET", "/api/chats")).chats);
    } catch (e) {
      setProblem((e as Error).message);
    }
  }, []);

  useEffect(() => {
    void refreshCollection();
    void refreshChats();
    const timer = window.setInterval(() => void refreshCollection(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [refreshCollection, refreshChats]);

  async function createChat(): Promise<number> {
    const chat = await api<Chat>("POST", "/api/chats");
    setChats((list) => [chat, ...list]);
    setActiveId(chat.id);
    return chat.id;
  }

  async function deleteChat(id: number) {
    if (!window.confirm("Delete this chat?")) return;
    try {
      await api("DELETE", `/api/chats/${id}`);
      setChats((list) => list.filter((chat) => chat.id !== id));
      setActiveId((active) => (active === id ? null : active));
    } catch (e) {
      setProblem((e as Error).message);
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

  if (state === null) {
    return (
      <main className="center">
        {offline ? `Tamra core unreachable: ${offline}` : "Connecting to Tamra…"}
      </main>
    );
  }
  const collection = state.collection;
  if (collection === null || changingFolder) {
    return (
      <main className="center">
        <Setup
          onDone={(next) => {
            setState(next);
            setChangingFolder(false);
          }}
          onCancel={collection ? () => setChangingFolder(false) : undefined}
        />
      </main>
    );
  }
  return (
    <div className="app">
      <header className="app-header">
        <h1>Tamra</h1>
        <IndexStatus
          collection={collection}
          index={state.index}
          disabled={busy}
          onChangeFolder={() => setChangingFolder(true)}
          onRebuild={() => void rebuild()}
        />
      </header>
      {(offline || problem) && (
        <p className="error banner" role="alert">
          {offline ? `Tamra core unreachable: ${offline}` : problem}
        </p>
      )}
      <ChatList
        chats={chats}
        activeId={activeId}
        disabled={busy}
        onSelect={(id) => {
          setProblem(null);
          setActiveId(id);
        }}
        onNew={() => {
          setProblem(null);
          setActiveId(null);
        }}
        onDelete={(id) => void deleteChat(id)}
      />
      <ChatView
        chatId={activeId}
        createChat={createChat}
        onBusyChange={setBusy}
        onAnswered={() => void refreshChats()}
      />
    </div>
  );
}
