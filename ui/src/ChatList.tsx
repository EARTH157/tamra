import type { Chat } from "./types";

type Props = {
  chats: Chat[];
  activeId: number | null;
  disabled: boolean;
  onSelect: (id: number) => void;
  onNew: () => void;
  onDelete: (id: number) => void;
};

export default function ChatList({ chats, activeId, disabled, onSelect, onNew, onDelete }: Props) {
  return (
    <nav className="chat-list">
      <button type="button" className="new-chat" onClick={onNew} disabled={disabled}>
        New chat
      </button>
      <ul>
        {chats.map((chat) => (
          <li key={chat.id} className={chat.id === activeId ? "active" : undefined}>
            <button
              type="button"
              className="chat-title"
              onClick={() => onSelect(chat.id)}
              disabled={disabled}
            >
              {chat.title || "New chat"}
            </button>
            <button
              type="button"
              className="delete"
              aria-label={`Delete ${chat.title || "chat"}`}
              onClick={() => onDelete(chat.id)}
              disabled={disabled}
            >
              ×
            </button>
          </li>
        ))}
      </ul>
    </nav>
  );
}
