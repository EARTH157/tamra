import { MoreHorizontal, Pencil, Trash2 } from "lucide-react";
import { type KeyboardEvent, useEffect, useLayoutEffect, useRef, useState } from "react";
import { useT } from "./i18n";
import type { Chat } from "./types";

type Props = {
  chats: Chat[];
  activeId: number | null;
  disabled: boolean;
  onSelect: (id: number) => void;
  onRename: (id: number, title: string) => Promise<void>;
  onDelete: (chat: Chat) => void;
};

/** The open menu: its chat and the ⋯ button's edges (viewport pixels). */
type MenuState = { id: number; above: number; below: number; left: number };

const GAP = 3; // between the ⋯ button and the menu
const MARGIN = 8; // the menu keeps this far from the window edges

/** The sidebar's chats, each with a ⋯ menu (Rename, Delete chat) and an inline rename. */
export default function ChatList({
  chats,
  activeId,
  disabled,
  onSelect,
  onRename,
  onDelete,
}: Props) {
  const t = useT();
  const untitled = (chat: Chat) => chat.title || t("chatList.untitled");
  const [menu, setMenu] = useState<MenuState | null>(null);
  const [editing, setEditing] = useState<number | null>(null);
  const trigger = useRef<HTMLElement | null>(null);

  function openMenu(chat: Chat, button: HTMLElement) {
    if (menu?.id === chat.id) {
      setMenu(null);
      return;
    }
    trigger.current = button;
    const rect = button.getBoundingClientRect();
    setMenu({ id: chat.id, above: rect.top, below: rect.bottom, left: rect.left + GAP });
  }

  const menuChat = menu ? chats.find((chat) => chat.id === menu.id) : undefined;
  return (
    <nav className="chat-list" aria-label={t("app.chats")}>
      <ul>
        {chats.map((chat) =>
          chat.id === editing ? (
            <RenameRow
              key={chat.id}
              chat={chat}
              onDone={() => setEditing(null)}
              onSave={(title) => onRename(chat.id, title)}
            />
          ) : (
            <li key={chat.id} className={chat.id === activeId ? "chat-row active" : "chat-row"}>
              <button
                type="button"
                className="chat-title"
                title={untitled(chat)}
                onClick={() => onSelect(chat.id)}
                disabled={disabled}
              >
                {untitled(chat)}
              </button>
              <button
                type="button"
                className="icon-button row-more"
                aria-label={t("chatList.options", { title: untitled(chat) })}
                aria-haspopup="menu"
                aria-expanded={menu?.id === chat.id}
                onClick={(event) => openMenu(chat, event.currentTarget)}
                disabled={disabled}
              >
                <MoreHorizontal size={16} />
              </button>
            </li>
          ),
        )}
      </ul>
      {menu && menuChat && (
        <ChatMenu
          above={menu.above}
          below={menu.below}
          left={menu.left}
          onClose={(refocus) => {
            setMenu(null);
            if (refocus) trigger.current?.focus();
          }}
          onRename={() => {
            setMenu(null);
            setEditing(menuChat.id);
          }}
          onDelete={() => {
            setMenu(null);
            onDelete(menuChat);
          }}
        />
      )}
    </nav>
  );
}

type MenuProps = {
  above: number;
  below: number;
  left: number;
  onClose: (refocus: boolean) => void;
  onRename: () => void;
  onDelete: () => void;
};

function ChatMenu({ above, below, left, onClose, onRename, onDelete }: MenuProps) {
  const t = useT();
  const box = useRef<HTMLDivElement>(null);
  const [place, setPlace] = useState({ top: below + GAP, left });

  // Keep the whole menu on screen: open below the ⋯ button, or above it when there is no
  // room, and clamp to the window in both directions.
  useLayoutEffect(() => {
    const rect = box.current?.getBoundingClientRect();
    if (!rect) return;
    const maxTop = window.innerHeight - rect.height - MARGIN;
    const maxLeft = window.innerWidth - rect.width - MARGIN;
    let top = below + GAP;
    if (top > maxTop) top = above - GAP - rect.height;
    setPlace({
      top: Math.max(MARGIN, Math.min(top, maxTop)),
      left: Math.max(MARGIN, Math.min(left, maxLeft)),
    });
  }, [above, below, left]);
  const close = useRef(onClose);
  useEffect(() => {
    close.current = onClose;
  });

  useEffect(() => {
    box.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
    function outside(event: MouseEvent) {
      const target = event.target as Node;
      if (box.current?.contains(target)) return;
      if (target instanceof Element && target.closest(".row-more")) return; // the toggle
      close.current(false);
    }
    document.addEventListener("mousedown", outside);
    return () => document.removeEventListener("mousedown", outside);
  }, []);

  function onKeyDown(event: KeyboardEvent) {
    const items = [...(box.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? [])];
    const at = items.indexOf(document.activeElement as HTMLElement);
    if (event.key === "Escape" || event.key === "Tab") {
      event.preventDefault();
      onClose(true); // back to the ⋯ button
    } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : -1;
      items[(at + step + items.length) % items.length]?.focus();
    }
  }

  return (
    <div
      ref={box}
      className="menu"
      role="menu"
      aria-label={t("chatList.menu")}
      style={place}
      onKeyDown={onKeyDown}
    >
      <button type="button" role="menuitem" className="menu-item" onClick={onRename}>
        <Pencil size={16} />
        {t("chatList.rename")}
      </button>
      <div className="menu-divider" role="separator" />
      <button type="button" role="menuitem" className="menu-item danger" onClick={onDelete}>
        <Trash2 size={16} />
        {t("chatList.delete")}
      </button>
    </div>
  );
}

type RenameProps = { chat: Chat; onSave: (title: string) => Promise<void>; onDone: () => void };

/** Inline rename: Enter saves, Esc (or leaving the field) cancels. */
function RenameRow({ chat, onSave, onDone }: RenameProps) {
  const t = useT();
  const [draft, setDraft] = useState(chat.title);
  const [tip, setTip] = useState<{ top: number; left: number } | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const saving = useRef(false);

  useLayoutEffect(() => {
    const field = input.current;
    if (!field) return;
    field.focus();
    field.select();
    const rect = field.getBoundingClientRect();
    setTip({ top: rect.bottom + 6, left: rect.left });
  }, []);

  async function save() {
    const title = draft.trim();
    if (saving.current) return;
    if (!title || title === chat.title) {
      onDone();
      return;
    }
    saving.current = true;
    try {
      await onSave(title);
    } finally {
      saving.current = false;
      onDone();
    }
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter" && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void save();
    } else if (event.key === "Escape") {
      event.preventDefault();
      onDone();
    }
  }

  return (
    <li className="chat-row renaming">
      <input
        ref={input}
        className="rename-input"
        aria-label={t("chatList.renameLabel")}
        value={draft}
        maxLength={200}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={onKeyDown}
        onBlur={() => {
          if (!saving.current) onDone();
        }}
      />
      <span className="tooltip" role="note" style={tip ?? undefined}>
        {t("chatList.renameHint")}
      </span>
    </li>
  );
}
