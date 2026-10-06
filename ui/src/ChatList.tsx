import { MoreHorizontal } from "lucide-react";
import { type KeyboardEvent, useLayoutEffect, useRef, useState } from "react";
import { useT } from "./i18n";
import type { Chat } from "./types";

type Props = {
  chats: Chat[];
  activeId: number | null;
  disabled: boolean;
  /** The chat being renamed in place, if any. */
  editingId: number | null;
  /** The chat whose ⋯ menu is open, if any. */
  menuId: number | null;
  onSelect: (id: number) => void;
  onOpenMenu: (chat: Chat, button: HTMLElement) => void;
  onRename: (id: number, title: string) => Promise<void>;
  onStopEditing: () => void;
};

/** The chats of one project, each with a ⋯ button (the menu is the tree's) and an inline rename. */
export default function ChatList({
  chats,
  activeId,
  disabled,
  editingId,
  menuId,
  onSelect,
  onOpenMenu,
  onRename,
  onStopEditing,
}: Props) {
  const t = useT();
  const untitled = (chat: Chat) => chat.title || t("chatList.untitled");
  return (
    <ul className="chat-rows">
      {chats.map((chat) => (
        <li key={chat.id}>
          {chat.id === editingId ? (
            <RenameRow
              value={chat.title}
              label={t("chatList.renameLabel")}
              maxLength={200}
              onDone={onStopEditing}
              onSave={(title) => onRename(chat.id, title)}
            />
          ) : (
            <div className={chat.id === activeId ? "chat-row active" : "chat-row"}>
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
                aria-expanded={menuId === chat.id}
                onClick={(event) => onOpenMenu(chat, event.currentTarget)}
                disabled={disabled}
              >
                <MoreHorizontal size={16} />
              </button>
            </div>
          )}
        </li>
      ))}
    </ul>
  );
}

type RenameProps = {
  value: string;
  /** The field's accessible name. */
  label: string;
  maxLength: number;
  onSave: (value: string) => Promise<void>;
  onDone: () => void;
};

/** Inline rename of a chat or a project: Enter saves, Esc (or leaving the field) cancels. */
export function RenameRow({ value, label, maxLength, onSave, onDone }: RenameProps) {
  const t = useT();
  const [draft, setDraft] = useState(value);
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
    const next = draft.trim();
    if (saving.current) return;
    if (!next || next === value) {
      onDone();
      return;
    }
    saving.current = true;
    try {
      await onSave(next);
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
    <div className="chat-row renaming">
      <input
        ref={input}
        className="rename-input"
        aria-label={label}
        value={draft}
        maxLength={maxLength}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={onKeyDown}
        onBlur={() => {
          if (!saving.current) onDone();
        }}
      />
      <span className="tooltip" role="note" style={tip ?? undefined}>
        {t("chatList.renameHint")}
      </span>
    </div>
  );
}
