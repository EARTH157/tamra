import { useState } from "react";
import Modal from "./Modal";
import type { Chat } from "./types";

type Props = { chat: Chat; onConfirm: () => Promise<void>; onCancel: () => void };

/** Confirm before a chat is deleted. */
export default function DeleteChatDialog({ chat, onConfirm, onCancel }: Props) {
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
      <h2 id="delete-dialog-title">Delete this chat?</h2>
      <p className="dialog-text">
        "{chat.title || "New chat"}" will be removed from this computer. Your documents are not
        changed.
      </p>
      <div className="dialog-actions">
        <button type="button" className="btn" onClick={onCancel} data-autofocus>
          Cancel
        </button>
        <button type="button" className="btn danger" onClick={confirm} disabled={working}>
          Delete chat
        </button>
      </div>
    </Modal>
  );
}
