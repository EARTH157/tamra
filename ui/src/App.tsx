import {
  AlertTriangle,
  FilePlus,
  Plug,
  Plus,
  RefreshCw,
  Search,
  SlidersHorizontal,
  X,
} from "lucide-react";
import {
  type CSSProperties,
  type DragEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { api } from "./api";
import { projectErrorText } from "./apiErrors";
import ChatSearch from "./ChatSearch";
import ChatView from "./ChatView";
import ConfirmDialog from "./ConfirmDialog";
import DeleteChatDialog from "./DeleteChatDialog";
import { useT } from "./i18n";
import IndexStatus from "./IndexStatus";
import { NewProjectDialog, ProjectSettingsDialog } from "./ProjectDialog";
import ProjectTree from "./ProjectTree";
import { useProjects } from "./projects";
import SettingsPage from "./SettingsPage";
import { useSettings } from "./settings";
import Setup from "./Setup";
import Splitter from "./Splitter";
import type { Chat, SettingsTab } from "./types";
import Welcome from "./Welcome";

/** The project dialog that is open, if any. At most one is open at a time. */
type Dialog =
  | { kind: "newProject" }
  | { kind: "settings"; id: number; focus?: "notes" }
  // Change folder, from the project's menu or from its settings (which come back afterwards).
  | { kind: "folder"; id: number; from: "menu" | "settings" }
  | { kind: "deleteProject"; id: number };

/** The sidebar's width at start, and how narrow and wide it can be dragged, in CSS pixels. */
export const SIDEBAR_WIDTH = 260;
const SIDEBAR_MIN = 200;
const SIDEBAR_MAX = 480;

export default function App() {
  const t = useT();
  const { settings } = useSettings();
  const [chats, setChats] = useState<Chat[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  // The project the user is working in: where "New chat" starts a chat, and whose card is shown.
  // It follows the chat that is opened and the project that is opened in the tree.
  const [projectId, setProjectId] = useState<number | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [dialog, setDialog] = useState<Dialog | null>(null);
  const [deleting, setDeleting] = useState<Chat | null>(null);
  const [searching, setSearching] = useState(false); // the Ctrl+K chat search is open
  // The chat stays mounted (but hidden) while Settings is open, so a streaming answer carries on.
  const [view, setView] = useState<"chat" | "settings">("chat");
  // shortcut: kept for this run only; save it in settings if people want it to stay.
  const [sidebarWidth, setSidebarWidth] = useState(SIDEBAR_WIDTH);
  const [settingsTab, setSettingsTab] = useState<SettingsTab>("general");
  const [dropping, setDropping] = useState(false); // files are being dragged over the window

  // Bumped when a chat is changed here (renamed, moved, new scope). A reload of the list that was
  // asked for before that change would bring the old chat back, so its answer is not used.
  const chatsVersion = useRef(0);

  const refreshChats = useCallback(async () => {
    try {
      for (let attempt = 0; attempt < 3; attempt++) {
        const version = chatsVersion.current;
        const loaded = (await api<{ chats: Chat[] }>("GET", "/api/chats")).chats;
        if (version === chatsVersion.current) {
          setChats(loaded);
          return;
        }
      }
    } catch (e) {
      setProblem((e as Error).message);
    }
  }, []);

  // When the core comes back after an outage it may have restarted: reload what it has.
  const projectsApi = useProjects(() => void refreshChats());
  const { projects, offline } = projectsApi;

  useEffect(() => {
    void refreshChats();
  }, [refreshChats]);

  const activeProject =
    projects?.find((project) => project.id === projectId) ?? projects?.[0] ?? null;
  const activeChat = chats.find((chat) => chat.id === activeId) ?? null;
  // The project named in "not found in …": the open chat's home, else the active project.
  const homeProject = projects?.find((project) => project.id === activeChat?.project_id);
  const dialogProject =
    dialog && dialog.kind !== "newProject"
      ? projects?.find((project) => project.id === dialog.id)
      : undefined;
  const deletedChats =
    dialog?.kind === "deleteProject"
      ? chats.filter((chat) => chat.project_id === dialog.id).length
      : 0;

  /** Create the chat for a first question: a general chat (no project) for a Chat-mode one. */
  async function createChat(general: boolean): Promise<number> {
    const home = activeProject ? { project_id: activeProject.id } : undefined;
    const body = general ? { general: true } : home;
    const chat = await api<Chat>("POST", "/api/chats", body);
    chatsVersion.current++; // a list asked for before this does not have the new chat
    setChats((list) => [chat, ...list]);
    setActiveId(chat.id);
    if (chat.project_id !== null) setProjectId(chat.project_id);
    return chat.id;
  }

  function selectChat(id: number) {
    setProblem(null);
    setActiveId(id);
    const home = chats.find((chat) => chat.id === id)?.project_id;
    if (home != null) setProjectId(home);
    setView("chat");
  }

  function newChat() {
    setProblem(null);
    // The current project already follows the open chat, or the project opened in the tree since
    // (which wins); with neither, the first project is only a default.
    setActiveId(null);
    setView("chat");
  }

  async function renameChat(id: number, title: string) {
    try {
      const chat = await api<Chat>("PATCH", `/api/chats/${id}`, { title });
      chatsVersion.current++;
      setChats((list) => list.map((item) => (item.id === id ? chat : item)));
    } catch (e) {
      setProblem(projectErrorText(e as Error, t));
    }
  }

  async function moveChat(chat: Chat, toProject: number) {
    try {
      const moved = await api<Chat>("PATCH", `/api/chats/${chat.id}`, { project_id: toProject });
      chatsVersion.current++;
      setChats((list) => list.map((item) => (item.id === chat.id ? moved : item)));
      if (chat.id === activeId) setProjectId(toProject);
    } catch (e) {
      setProblem(projectErrorText(e as Error, t));
    }
  }

  async function deleteChat(id: number) {
    try {
      await api("DELETE", `/api/chats/${id}`);
      chatsVersion.current++;
      setChats((list) => list.filter((chat) => chat.id !== id));
      setActiveId((active) => (active === id ? null : active));
    } catch (e) {
      setProblem((e as Error).message);
    } finally {
      setDeleting(null);
    }
  }

  /** Delete a chat: after the dialog, or at once when the setting says not to ask. */
  function requestDelete(chat: Chat) {
    if (settings.ask_before_delete) setDeleting(chat);
    else void deleteChat(chat.id);
  }

  async function createProject(name: string, folder: string) {
    const project = await projectsApi.create(name, folder);
    setProjectId(project.id);
    setDialog(null);
    void refreshChats(); // a chat made before any project now belongs to the first one
  }

  async function renameProject(id: number, name: string) {
    try {
      await projectsApi.change(id, { name });
    } catch (e) {
      setProblem(projectErrorText(e as Error, t));
    }
  }

  /** Rebuild from the menu or the card: a refusal shows in the banner. */
  async function rebuildProject(id: number) {
    try {
      await projectsApi.rebuild(id);
    } catch (e) {
      setProblem(projectErrorText(e as Error, t));
    }
  }

  async function deleteProject(id: number) {
    try {
      await projectsApi.remove(id);
    } catch (e) {
      throw new Error(projectErrorText(e as Error, t)); // shown in the dialog, which stays open
    }
    if (activeChat?.project_id === id) setActiveId(null);
    // The current project is gone: the open chat's project takes over, else the first one (null).
    const other = activeChat?.project_id;
    setProjectId((current) => (current !== id ? current : other != null && other !== id ? other : null));
    setDialog(null);
    await refreshChats();
  }

  function openSettings(tab: SettingsTab) {
    setSettingsTab(tab);
    setView("settings");
  }

  /** Every chat was deleted from Settings: reload the list and leave the open chat. */
  async function chatsDeleted() {
    setActiveId(null);
    await refreshChats();
  }

  /** Files dragged onto the window go into the current project's folder, and are indexed there.
   * Every file drag is taken, so a file dropped by mistake never opens in place of the app. */
  function dragOver(e: DragEvent) {
    if (!e.dataTransfer.types.includes("Files")) return; // text dragged inside the app
    e.preventDefault();
    e.dataTransfer.dropEffect = activeProject ? "copy" : "none";
    setDropping(true);
  }

  async function drop(e: DragEvent) {
    if (!e.dataTransfer.types.includes("Files")) return;
    e.preventDefault();
    setDropping(false);
    if (!activeProject) return;
    setProblem(null);
    const refused = await projectsApi.addFiles(activeProject.id, [...e.dataTransfer.files]);
    if (refused.length) {
      const list = refused.map(({ file, error }) => `${file.name} (${projectErrorText(error, t)})`);
      setProblem(t("drop.refused", { list: list.join("; ") }));
    }
  }

  // Ctrl+K searches the chats and Ctrl+N starts one, wherever focus is. The physical key decides,
  // so a Thai keyboard layout works too. Not while an answer is written or another dialog is open.
  const shortcut = useRef<(event: KeyboardEvent) => void>(() => {});
  shortcut.current = (event) => {
    if (!(event.ctrlKey || event.metaKey) || event.altKey || event.shiftKey) return;
    if (event.code !== "KeyK" && event.code !== "KeyN") return;
    event.preventDefault(); // Ctrl+N would open a new window
    if (busy || dialog || deleting || searching || !projects?.length) return;
    if (event.code === "KeyK") setSearching(chats.length > 0);
    else newChat();
  };
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => shortcut.current(event);
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, []);

  /** Leave the folder dialog: back to the project's settings if it was opened from there. */
  function leaveFolderDialog(current: Dialog & { kind: "folder" }) {
    setDialog(current.from === "settings" ? { kind: "settings", id: current.id } : null);
  }

  return (
    <div
      className="app"
      style={{ "--sidebar-width": `${sidebarWidth}px` } as CSSProperties}
      onDragOver={dragOver}
      onDragLeave={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setDropping(false);
      }}
      onDrop={(e) => void drop(e)}
    >
      {dropping && (
        <div className="drop-overlay" aria-hidden="true">
          <div className="drop-card">
            <FilePlus size={28} />
            {activeProject ? t("drop.hint", { name: activeProject.name }) : t("drop.noProject")}
          </div>
        </div>
      )}
      <aside className="sidebar">
        <div className="brand">
          {/* The logo's glyph (no tile), as in the Figma lockup; the brackets follow the theme's text. */}
          <svg className="brand-mark" viewBox="-6.41 -6.41 76 76" fill="none" strokeWidth={6.65} aria-hidden="true">
            <path d="M18.29 3.33H3.33V59.85H18.29M44.89 3.33H59.85V59.85H44.89" stroke="currentColor" />
            <path d="M19.95 19.95H43.23M31.59 19.95V48.21" stroke="var(--logo-t)" />
          </svg>
          <span className="brand-name">Tamra</span>
          <span className="brand-thai" lang="th">
            ตำรา
          </span>
        </div>
        <div className="new-chat-row">
          <button
            type="button"
            className="new-chat"
            title={t("app.newChatKey")}
            onClick={newChat}
            disabled={busy || !projects?.length}
          >
            <Plus size={16} />
            {t("app.newChat")}
          </button>
          <button
            type="button"
            className="new-chat search-chats"
            aria-label={t("search.open")}
            title={t("search.open")}
            onClick={() => setSearching(true)}
            disabled={busy || !chats.length}
          >
            <Search size={16} />
          </button>
        </div>
        <ProjectTree
          projects={projects}
          chats={chats}
          activeChatId={activeId}
          activeProjectId={activeProject?.id ?? null}
          revealProjectId={projectId}
          busy={busy}
          onSelectChat={selectChat}
          onSelectProject={setProjectId}
          onNewProject={() => setDialog({ kind: "newProject" })}
          onRenameProject={renameProject}
          onProjectSettings={(project) => setDialog({ kind: "settings", id: project.id })}
          onChangeFolder={(project) => setDialog({ kind: "folder", id: project.id, from: "menu" })}
          onRebuild={(project) => void rebuildProject(project.id)}
          onDeleteProject={(project) => setDialog({ kind: "deleteProject", id: project.id })}
          onRenameChat={renameChat}
          onMoveChat={(chat, toProject) => void moveChat(chat, toProject)}
          onDeleteChat={requestDelete}
        />
        <div className="sidebar-fill" />
        {activeProject && (
          <IndexStatus
            project={activeProject}
            disabled={busy}
            onChangeFolder={() => setDialog({ kind: "folder", id: activeProject.id, from: "menu" })}
            onRebuild={() => void rebuildProject(activeProject.id)}
            onOpenModelSettings={() => openSettings("model")}
            onNotes={() => setDialog({ kind: "settings", id: activeProject.id, focus: "notes" })}
          />
        )}
        {settings.mcp_enabled && (
          <button type="button" className="sidebar-mcp" onClick={() => openSettings("general")}>
            <Plug size={14} />
            {t("mcp.badge")}
          </button>
        )}
        <button
          type="button"
          className="sidebar-settings"
          aria-current={view === "settings" ? "page" : undefined}
          onClick={() => (view === "settings" ? setView("chat") : openSettings("general"))}
        >
          <SlidersHorizontal size={16} />
          {t("settings.open")}
        </button>
      </aside>
      <Splitter
        label={t("app.resizeSidebar")}
        orientation="vertical"
        size={sidebarWidth}
        min={SIDEBAR_MIN}
        max={() => Math.min(SIDEBAR_MAX, window.innerWidth / 3)}
        grow={1}
        onResize={setSidebarWidth}
      />
      <main className="main">
        {offline ? (
          <div className="banner" role="alert">
            <AlertTriangle size={16} />
            <span className="banner-text" title={offline}>
              {t("app.coreUnreachable")}
            </span>
            <button type="button" className="retry" onClick={() => void projectsApi.refresh()}>
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
        <div className="view-slot" hidden={view === "settings"}>
          {projects === null ? (
            <div className="connecting">{offline ? null : t("app.connecting")}</div>
          ) : projects.length === 0 ? (
            <Welcome onChoose={() => setDialog({ kind: "newProject" })} />
          ) : (
            <ChatView
              chatId={activeId}
              collectionName={(homeProject ?? activeProject)?.name}
              createChat={createChat}
              onBusyChange={setBusy}
              onAnswered={() => void refreshChats()}
              onOpenSettings={openSettings}
              visible={view === "chat"}
              projects={projects}
              chat={activeChat}
              defaultProjectId={activeProject?.id ?? null}
              onChatChanged={(updated) => {
                chatsVersion.current++;
                setChats((list) => list.map((item) => (item.id === updated.id ? updated : item)));
              }}
            />
          )}
        </div>
        {view === "settings" && (
          <SettingsPage
            tab={settingsTab}
            onTabChange={setSettingsTab}
            chatCount={chats.length}
            busy={busy}
            onChatsDeleted={chatsDeleted}
          />
        )}
      </main>
      {dialog?.kind === "newProject" && (
        <NewProjectDialog onCreate={createProject} onCancel={() => setDialog(null)} />
      )}
      {dialog?.kind === "settings" && dialogProject && (
        <ProjectSettingsDialog
          project={dialogProject}
          busy={busy}
          focusNotes={dialog.focus === "notes"}
          onSave={async (changes) => {
            await projectsApi.change(dialogProject.id, changes);
            setDialog(null);
          }}
          onChangeFolder={() =>
            setDialog({ kind: "folder", id: dialogProject.id, from: "settings" })
          }
          onRebuild={() => projectsApi.rebuild(dialogProject.id)}
          onClose={() => setDialog(null)}
        />
      )}
      {dialog?.kind === "folder" && dialogProject && (
        <Setup
          initialFolder={dialogProject.folder_path}
          onSubmit={async (folder) => {
            await projectsApi.changeFolder(dialogProject.id, folder);
            leaveFolderDialog(dialog);
          }}
          onCancel={() => leaveFolderDialog(dialog)}
        />
      )}
      {dialog?.kind === "deleteProject" && dialogProject && (
        <ConfirmDialog
          title={t("project.deleteTitle")}
          text={t(deletedChats === 0 ? "project.deleteTextNone" : "project.deleteText", {
            name: dialogProject.name,
            count: deletedChats,
          })}
          confirmLabel={t("project.delete")}
          danger
          onConfirm={() => deleteProject(dialogProject.id)}
          onCancel={() => setDialog(null)}
        />
      )}
      {deleting && (
        <DeleteChatDialog
          chat={deleting}
          onConfirm={() => deleteChat(deleting.id)}
          onCancel={() => setDeleting(null)}
        />
      )}
      {searching && projects && (
        <ChatSearch
          chats={chats}
          projects={projects}
          onOpen={(id) => {
            setSearching(false);
            selectChat(id);
          }}
          onClose={() => setSearching(false)}
        />
      )}
    </div>
  );
}
