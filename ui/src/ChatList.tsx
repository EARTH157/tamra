import { MoreHorizontal } from "lucide-react";
import { useT } from "./i18n";
import RenameRow from "./RenameRow";
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
