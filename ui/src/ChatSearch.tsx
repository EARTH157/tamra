import { MessageSquare, Search } from "lucide-react";
import { type KeyboardEvent, useEffect, useRef, useState } from "react";
import { api } from "./api";
import { useT } from "./i18n";
import Modal from "./Modal";
import type { Chat, ChatMatch, Project } from "./types";

/** How long typing must pause before the core is asked, and how many recent chats show first. */
const DEBOUNCE_MS = 150;
const RECENT = 8;

type Props = {
  /** Every chat, newest activity first: the recent ones show before anything is typed. */
  chats: Chat[];
  projects: Project[];
  onOpen: (id: number) => void;
  onClose: () => void;
};

/** Find a chat by its title or what was said in it (Ctrl+K), and open it with Enter. */
export default function ChatSearch({ chats, projects, onOpen, onClose }: Props) {
  const t = useT();
  const [query, setQuery] = useState("");
  const [found, setFound] = useState<ChatMatch[] | null>(null); // null: nothing typed yet
  const [failed, setFailed] = useState(false);
  const [active, setActive] = useState(0);
  const seq = useRef(0); // only the newest search shows when it answers

  const wanted = query.trim();
  useEffect(() => {
    const mine = ++seq.current;
    if (!wanted) {
      setFound(null);
      setFailed(false);
      return;
    }
    const timer = window.setTimeout(() => {
      api<{ chats: ChatMatch[] }>("GET", `/api/chats/search?q=${encodeURIComponent(wanted)}`)
        .then((result) => {
          if (mine !== seq.current) return;
          setFound(result.chats);
          setFailed(false);
        })
        .catch(() => {
          if (mine === seq.current) setFailed(true);
        });
    }, DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [wanted]);

  const rows: ChatMatch[] = found ?? chats.slice(0, RECENT).map((chat) => ({ ...chat, match: null }));
  useEffect(() => setActive(0), [found]);

  function open(row: ChatMatch | undefined) {
    if (row) onOpen(row.id);
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : -1;
      setActive((i) => (rows.length ? (i + step + rows.length) % rows.length : 0));
    } else if (event.key === "Enter" && !event.nativeEvent.isComposing) {
      event.preventDefault();
      open(rows[active]);
    }
  }

  const projectName = (chat: Chat) => projects.find((p) => p.id === chat.project_id)?.name;
  const optionId = (id: number) => `chat-search-${id}`;

  return (
    <Modal labelledBy="chat-search-title" onClose={onClose}>
      <h2 id="chat-search-title" className="chat-search-title">
        {t("search.title")}
      </h2>
      <div className="chat-search-field">
        <Search size={16} aria-hidden="true" />
        <input
          data-autofocus
          type="search"
          role="combobox"
          aria-expanded="true"
          aria-controls="chat-search-results"
          aria-activedescendant={rows[active] ? optionId(rows[active].id) : undefined}
          aria-label={t("search.title")}
          placeholder={t("search.placeholder")}
          value={query}
          maxLength={200}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={onKeyDown}
        />
      </div>
      <p className="chat-search-label">
        {failed ? t("search.failed") : found === null ? t("search.recent") : null}
      </p>
      {found !== null && found.length === 0 && !failed ? (
        <p className="chat-search-empty">{t("search.none", { query: wanted })}</p>
      ) : (
        <ul id="chat-search-results" className="chat-search-results" role="listbox" aria-label={t("search.title")}>
          {rows.map((row, i) => (
            <li
              key={row.id}
              id={optionId(row.id)}
              role="option"
              aria-selected={i === active}
              className={i === active ? "active" : undefined}
              onMouseMove={() => setActive(i)}
              onClick={() => open(row)}
            >
              <MessageSquare size={15} aria-hidden="true" />
              <span className="chat-search-text">
                <span className="chat-search-row-head">
                  <span className="chat-search-name">{row.title || t("chatList.untitled")}</span>
                  {projectName(row) && <span className="chat-search-project">{projectName(row)}</span>}
                </span>
                {row.match && <span className="chat-search-match">{row.match}</span>}
              </span>
            </li>
          ))}
        </ul>
      )}
      <p className="chat-search-foot">{t("search.keys")}</p>
    </Modal>
  );
}
