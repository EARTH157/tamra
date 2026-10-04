import { useState } from "react";
import { useT } from "./i18n";
import Modal from "./Modal";
import type { Chat } from "./types";

type Props = { chat: Chat; onConfirm: () => Promise<void>; onCancel: () => void };

/** Confirm before a chat is deleted. */
export default function DeleteChatDialog({ chat, onConfirm, onCancel }: Props) {
  const t = useT();
  const [working, setWorking] = useState(false);

  async function confirm() {
    setWorking(true);
    try {
      await onConfirm();
    } finally {
      setWorking(false);
    }
  }

  return (
    <Modal labelledBy="delete-dialog-title" onClose={onCancel} small>
      <h2 id="delete-dialog-title">{t("deleteChat.title")}</h2>
      <p className="dialog-text">
        {t("deleteChat.text", { title: chat.title || t("chatList.untitled") })}
      </p>
      <div className="dialog-actions">
        <button type="button" className="btn" onClick={onCancel} data-autofocus>
          {t("common.cancel")}
        </button>
        <button type="button" className="btn danger" onClick={confirm} disabled={working}>
          {t("deleteChat.confirm")}
        </button>
      </div>
    </Modal>
  );
}
