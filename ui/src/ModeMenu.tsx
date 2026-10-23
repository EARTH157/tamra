import { AlignLeft, Check, ChevronDown, MessageCircle } from "lucide-react";
import { useState } from "react";
import { useT } from "./i18n";
import PopMenu from "./PopMenu";
import type { AnswerMode } from "./types";

type Props = { mode: AnswerMode; onChange: (mode: AnswerMode) => void };

const iconOf = (mode: AnswerMode) => (mode === "chat" ? MessageCircle : AlignLeft);

/** The "Answer" chip and its menu: write an answer, only show the matching passages, or chat. */
export default function ModeMenu({ mode, onChange }: Props) {
  const t = useT();
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const options: { mode: AnswerMode; name: string; text: string }[] = [
    { mode: "answer", name: t("chat.mode.answer"), text: t("chat.mode.answerText") },
    { mode: "search", name: t("chat.mode.search"), text: t("chat.mode.searchText") },
    { mode: "chat", name: t("chat.mode.chat"), text: t("chat.mode.chatText") },
  ];
  const current = options.find((option) => option.mode === mode) ?? options[0];
  const CurrentIcon = iconOf(current.mode);

  return (
    <>
      <button
        type="button"
        className={anchor ? "chip open" : "chip"}
        aria-haspopup="menu"
        aria-expanded={anchor !== null}
        aria-label={`${t("chat.mode.menu")}: ${current.name}`}
        onClick={(event) => setAnchor(anchor ? null : event.currentTarget)}
      >
        <CurrentIcon size={14} />
        {current.name}
        <ChevronDown size={13} className="chip-chevron" />
      </button>
      {anchor && (
        <PopMenu
          anchor={anchor}
          label={t("chat.mode.menu")}
          className="wide"
          onClose={(refocus) => {
            setAnchor(null);
            if (refocus) anchor.focus();
          }}
        >
          <div className="menu-heading">{t("chat.mode.menu")}</div>
          {options.map((option) => {
            const OptionIcon = iconOf(option.mode);
            return (
              <button
                key={option.mode}
                type="button"
                role="menuitemradio"
                aria-checked={option.mode === mode}
                className="menu-entry"
                onClick={() => {
                  setAnchor(null);
                  anchor.focus();
                  onChange(option.mode);
                }}
              >
                <OptionIcon size={16} className="entry-icon" />
                <span className="entry-text">
                  <span className="entry-name">{option.name}</span>
                  <span className="entry-note">{option.text}</span>
                </span>
                {option.mode === mode && <Check size={16} className="entry-check" />}
              </button>
            );
          })}
        </PopMenu>
      )}
    </>
  );
}
