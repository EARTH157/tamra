import { useState } from "react";
import { useT } from "./i18n";
import Modal from "./Modal";

type Props = {
  title: string;
  text: string;
  confirmLabel: string;
  /** Red confirm button, for something that cannot be undone. */
  danger?: boolean;
  /** Resolve to close the dialog; reject with an Error to show its message and stay open. */
  onConfirm: () => Promise<void>;
  onCancel: () => void;
};

/** A small yes/no dialog. Cancel has the focus, so Enter never confirms by accident. */
export default function ConfirmDialog({
  title,
  text,
  confirmLabel,
  danger,
  onConfirm,
  onCancel,
}: Props) {
  const t = useT();
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function confirm() {
    setWorking(true);
    setError(null);
    try {
      await onConfirm();
    } catch (e) {
      setError((e as Error).message);
      setWorking(false);
    }
  }

  return (
    <Modal labelledBy="confirm-dialog-title" onClose={onCancel} small>
      <h2 id="confirm-dialog-title">{title}</h2>
      <p className="dialog-text">{text}</p>
      {error && (
        <p className="field-error" role="alert">
          {error}
        </p>
      )}
      <div className="dialog-actions">
        <button type="button" className="btn" onClick={onCancel} data-autofocus>
          {t("common.cancel")}
        </button>
        <button
          type="button"
          className={danger ? "btn danger" : "btn primary"}
          onClick={() => void confirm()}
          disabled={working}
        >
          {confirmLabel}
        </button>
      </div>
    </Modal>
  );
}
