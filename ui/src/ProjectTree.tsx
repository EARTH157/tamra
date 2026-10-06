import {
  ChevronDown,
  ChevronRight,
  Folder,
  FolderInput,
  MoreHorizontal,
  Pencil,
  Plus,
  RefreshCw,
  SlidersHorizontal,
  Trash2,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import ChatList from "./ChatList";
import { useT } from "./i18n";
import { NAME_MAX } from "./projects";
import RenameRow from "./RenameRow";
import RowMenu, { SubMenu } from "./RowMenu";
import type { Chat, Project } from "./types";

const STORAGE_KEY = "tamra.expandedProjects";

/** The ids of the projects that were left open, from the last run; null when none were saved. */
function loadExpanded(): Set<number> | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw === null) return null;
    const saved: unknown = JSON.parse(raw);
    if (Array.isArray(saved)) return new Set(saved.filter((id) => typeof id === "number"));
  } catch {
    // storage unavailable or not JSON: as if nothing was saved
  }
  return null;
}

function saveExpanded(expanded: Set<number>) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify([...expanded]));
  } catch {
    // storage unavailable: the choice only lasts for this run
  }
}

/**
 * Which projects are open; the set is remembered. A project is opened whenever `revealId` changes
 * to it (a chat was opened in it, or it was clicked). On a first launch, with nothing saved, the
 * default project (`firstId`) is opened once; saved choices are never overridden.
 */
function useExpanded(revealId: number | null, firstId: number | null) {
  const [initial] = useState(loadExpanded);
  const [expanded, setExpanded] = useState(() => initial ?? new Set<number>());
  const firstLaunch = useRef(initial === null);

  function change(id: number, open: boolean) {
    firstLaunch.current = false; // from now on the user's choices are the state
    setExpanded((before) => {
      if (before.has(id) === open) return before;
      const next = new Set(before);
      if (open) next.add(id);
      else next.delete(id);
      saveExpanded(next);
      return next;
    });
  }

  useEffect(() => {
    if (revealId !== null) change(revealId, true);
  }, [revealId]);

  useEffect(() => {
    if (firstLaunch.current && firstId !== null) change(firstId, true);
  }, [firstId]);

  return { expanded, change };
}

/** The open ⋯ menu: what it belongs to and its button's edges (viewport pixels). */
type MenuState = { kind: "project" | "chat"; id: number; above: number; below: number; left: number };
type Editing = { kind: "project" | "chat"; id: number };

const MENU_GAP = 3; // the menu starts this far right of the button's left edge

type Props = {
  /** Null until the core has answered. */
  projects: Project[] | null;
  chats: Chat[];
  activeChatId: number | null;
  /** The project in use, shown in bold (it may be the first one, by default). */
  activeProjectId: number | null;
  /** The project to open: the one the user chose or opened a chat in, not a default. */
  revealProjectId: number | null;
  /** An answer is being written: chats cannot be switched, and indexes and folders cannot change. */
  busy: boolean;
  onSelectChat: (id: number) => void;
  onSelectProject: (id: number) => void;
  onNewProject: () => void;
  onRenameProject: (id: number, name: string) => Promise<void>;
  onProjectSettings: (project: Project) => void;
  onChangeFolder: (project: Project) => void;
  onRebuild: (project: Project) => void;
  onDeleteProject: (project: Project) => void;
  onRenameChat: (id: number, title: string) => Promise<void>;
  onMoveChat: (chat: Chat, projectId: number) => void;
  onDeleteChat: (chat: Chat) => void;
};

/**
 * The sidebar: a PROJECTS heading with a + button, then each project with its chats beneath it.
 * One ⋯ menu is open at a time, for a project (settings, rename, folder, index, delete) or for a
 * chat (rename, move to another project, delete).
 */
export default function ProjectTree({
  projects,
  chats,
  activeChatId,
  activeProjectId,
  revealProjectId,
  busy,
  onSelectChat,
  onSelectProject,
  onNewProject,
  onRenameProject,
  onProjectSettings,
  onChangeFolder,
  onRebuild,
  onDeleteProject,
  onRenameChat,
  onMoveChat,
  onDeleteChat,
}: Props) {
  const t = useT();
  const { expanded, change } = useExpanded(revealProjectId, activeProjectId);
  const [menu, setMenu] = useState<MenuState | null>(null);
  const [editing, setEditing] = useState<Editing | null>(null);
  const trigger = useRef<HTMLElement | null>(null);

  function openMenu(kind: MenuState["kind"], id: number, button: HTMLElement) {
    if (menu?.kind === kind && menu.id === id) {
      setMenu(null);
      return;
    }
    trigger.current = button;
    const rect = button.getBoundingClientRect();
    setMenu({ kind, id, above: rect.top, below: rect.bottom, left: rect.left + MENU_GAP });
  }

  function closeMenu(refocus: boolean) {
    setMenu(null);
    if (refocus) trigger.current?.focus();
  }

  function toggle(project: Project) {
    const open = expanded.has(project.id);
    change(project.id, !open);
    if (!open) onSelectProject(project.id); // opening a project makes it the one new chats go to
  }

  // Chats with no home project: made before any project existed, and not yet adopted by one.
  const homeless = chats.filter((chat) => chat.project_id === null);
  const chatList = (list: Chat[]) => (
    <ChatList
      chats={list}
      activeId={activeChatId}
      disabled={busy}
      editingId={editing?.kind === "chat" ? editing.id : null}
      menuId={menu?.kind === "chat" ? menu.id : null}
      onSelect={onSelectChat}
      onOpenMenu={(chat, button) => openMenu("chat", chat.id, button)}
      onRename={onRenameChat}
      onStopEditing={() => setEditing(null)}
    />
  );

  const menuProject = menu?.kind === "project" ? projects?.find((p) => p.id === menu.id) : undefined;
  const menuChat = menu?.kind === "chat" ? chats.find((c) => c.id === menu.id) : undefined;
  // The projects a chat can move to: every one but its home.
  const targets = (projects ?? []).filter((project) => project.id !== menuChat?.project_id);

  return (
    <>
      <div className="section-head">
        <span className="section-label">{t("app.projects")}</span>
        <button
          type="button"
          className="icon-button"
          aria-label={t("app.newProject")}
          title={t("app.newProject")}
          onClick={onNewProject}
        >
          <Plus size={16} />
        </button>
      </div>
      {projects !== null && projects.length === 0 && (
        <p className="sidebar-note">{t("app.noProjects")}</p>
      )}
      <nav className="project-tree" aria-label={t("projectTree.label")}>
        <ul>
          {projects?.map((project) => {
            const open = expanded.has(project.id);
            const mine = chats.filter((chat) => chat.project_id === project.id);
            const indexed = project.index.counts.indexed;
            return (
              <li key={project.id}>
                {editing?.kind === "project" && editing.id === project.id ? (
                  <RenameRow
                    value={project.name}
                    label={t("project.nameLabel")}
                    maxLength={NAME_MAX}
                    onDone={() => setEditing(null)}
                    onSave={(name) => onRenameProject(project.id, name)}
                  />
                ) : (
                  <div className={project.id === activeProjectId ? "project-row active" : "project-row"}>
                    <button
                      type="button"
                      className="project-toggle"
                      title={project.name}
                      aria-expanded={open}
                      onClick={() => toggle(project)}
                    >
                      {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                      <Folder size={16} />
                      <span className="project-name">{project.name}</span>
                    </button>
                    <span className="project-count" title={t("index.filesIndexed", { count: indexed })}>
                      {indexed}
                    </span>
                    <button
                      type="button"
                      className="icon-button row-more"
                      aria-label={t("projectTree.options", { name: project.name })}
                      aria-haspopup="menu"
                      aria-expanded={menu?.kind === "project" && menu.id === project.id}
                      onClick={(event) => openMenu("project", project.id, event.currentTarget)}
                    >
                      <MoreHorizontal size={16} />
                    </button>
                  </div>
                )}
                {open &&
                  (mine.length === 0 ? (
                    <p className="project-empty">{t("projectTree.noChats")}</p>
                  ) : (
                    chatList(mine)
                  ))}
              </li>
            );
          })}
        </ul>
        {homeless.length > 0 && (
          <div className="project-group">
            <div className="project-group-label">{t("projectTree.otherChats")}</div>
            {chatList(homeless)}
          </div>
        )}
      </nav>
      {menu && menuProject && (
        <RowMenu
          above={menu.above}
          below={menu.below}
          left={menu.left}
          label={t("projectTree.options", { name: menuProject.name })}
          heading={menuProject.name}
          onClose={closeMenu}
        >
          <button
            type="button"
            role="menuitem"
            className="menu-item"
            onClick={() => {
              setMenu(null);
              onProjectSettings(menuProject);
            }}
          >
            <SlidersHorizontal size={16} />
            {t("project.settings")}
          </button>
          <button
            type="button"
            role="menuitem"
            className="menu-item"
            onClick={() => {
              setMenu(null);
              setEditing({ kind: "project", id: menuProject.id });
            }}
          >
            <Pencil size={16} />
            {t("chatList.rename")}
          </button>
          <button
            type="button"
            role="menuitem"
            className="menu-item"
            disabled={busy}
            onClick={() => {
              setMenu(null);
              onChangeFolder(menuProject);
            }}
          >
            <Folder size={16} />
            {t("index.changeFolder")}
          </button>
          <button
            type="button"
            role="menuitem"
            className="menu-item"
            disabled={busy}
            onClick={() => {
              setMenu(null);
              onRebuild(menuProject);
            }}
          >
            <RefreshCw size={16} />
            {t("index.rebuild")}
          </button>
          <div className="menu-divider" role="separator" />
          <button
            type="button"
            role="menuitem"
            className="menu-item danger"
            disabled={busy}
            onClick={() => {
              setMenu(null);
              onDeleteProject(menuProject);
            }}
          >
            <Trash2 size={16} />
            {t("project.delete")}
          </button>
        </RowMenu>
      )}
      {menu && menuChat && (
        <RowMenu
          above={menu.above}
          below={menu.below}
          left={menu.left}
          label={t("chatList.options", { title: menuChat.title || t("chatList.untitled") })}
          onClose={closeMenu}
        >
          <button
            type="button"
            role="menuitem"
            className="menu-item"
            onClick={() => {
              setMenu(null);
              setEditing({ kind: "chat", id: menuChat.id });
            }}
          >
            <Pencil size={16} />
            {t("chatList.rename")}
          </button>
          <SubMenu
            label={t("chatList.moveToProject")}
            icon={<FolderInput size={16} />}
            disabled={targets.length === 0}
          >
            {targets.map((project) => (
              <button
                key={project.id}
                type="button"
                role="menuitem"
                className="menu-item"
                title={project.name}
                onClick={() => {
                  setMenu(null);
                  change(project.id, true);
                  onMoveChat(menuChat, project.id);
                }}
              >
                <Folder size={16} />
                <span className="menu-item-label">{project.name}</span>
              </button>
            ))}
          </SubMenu>
          <div className="menu-divider" role="separator" />
          <button
            type="button"
            role="menuitem"
            className="menu-item danger"
            onClick={() => {
              setMenu(null);
              onDeleteChat(menuChat);
            }}
          >
            <Trash2 size={16} />
            {t("chatList.delete")}
          </button>
        </RowMenu>
      )}
    </>
  );
}
