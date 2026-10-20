import { AlertTriangle, BookOpen, Plus, RefreshCw, SlidersHorizontal, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import { projectErrorText } from "./apiErrors";
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
import type { Chat, SettingsTab } from "./types";
import Welcome from "./Welcome";

/** The project dialog that is open, if any. At most one is open at a time. */
type Dialog =
  | { kind: "newProject" }
  | { kind: "settings"; id: number }
  // Change folder, from the project's menu or from its settings (which come back afterwards).
  | { kind: "folder"; id: number; from: "menu" | "settings" }
  | { kind: "deleteProject"; id: number };

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
  // The chat stays mounted (but hidden) while Settings is open, so a streaming answer carries on.
  const [view, setView] = useState<"chat" | "settings">("chat");
  const [settingsTab, setSettingsTab] = useState<SettingsTab>("general");

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

  async function createChat(): Promise<number> {
    const chat = await api<Chat>(
      "POST",
      "/api/chats",
      activeProject ? { project_id: activeProject.id } : undefined,
    );
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
      await projectsApi.rename(id, name);
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

  /** Leave the folder dialog: back to the project's settings if it was opened from there. */
  function leaveFolderDialog(current: Dialog & { kind: "folder" }) {
    setDialog(current.from === "settings" ? { kind: "settings", id: current.id } : null);
  }

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
        <button type="button" className="new-chat" onClick={newChat} disabled={busy || !projects?.length}>
          <Plus size={16} />
          {t("app.newChat")}
        </button>
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
          />
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
          onRename={async (name) => {
            await projectsApi.rename(dialogProject.id, name);
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
    </div>
  );
}
