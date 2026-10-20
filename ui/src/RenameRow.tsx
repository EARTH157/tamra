import { type KeyboardEvent, useLayoutEffect, useRef, useState } from "react";
import { useT } from "./i18n";

type RenameProps = {
  value: string;
  /** The field's accessible name. */
  label: string;
  maxLength: number;
  onSave: (value: string) => Promise<void>;
  onDone: () => void;
};

/** Inline rename of a chat or a project: Enter saves, Esc (or leaving the field) cancels. */
export default function RenameRow({ value, label, maxLength, onSave, onDone }: RenameProps) {
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
