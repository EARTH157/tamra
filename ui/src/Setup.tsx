import { X } from "lucide-react";
import { type FormEvent, useState } from "react";
import { projectErrorText } from "./apiErrors";
import FolderField from "./FolderField";
import { useT } from "./i18n";
import Modal from "./Modal";

type Props = {
  /** Use the folder: resolve to close the dialog; reject with an ApiError to show why it was refused. */
  onSubmit: (folder: string) => Promise<void>;
  onCancel: () => void;
  initialFolder?: string;
};

/** The folder dialog: change the folder of documents a project indexes. */
export default function Setup({ onSubmit, onCancel, initialFolder = "" }: Props) {
  const t = useT();
  const [folder, setFolder] = useState(initialFolder);
  const [error, setError] = useState<string | null>(null);
  const [working, setWorking] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setWorking(true);
    setError(null);
    try {
      await onSubmit(folder.trim());
    } catch (e) {
      setError(projectErrorText(e as Error, t));
      setWorking(false);
    }
  }

  return (
    <Modal labelledBy="folder-dialog-title" onClose={onCancel}>
      <form onSubmit={submit}>
        <div className="dialog-header">
          <h2 id="folder-dialog-title">{t("folder.changeTitle")}</h2>
          <button type="button" className="icon-button" aria-label={t("common.close")} onClick={onCancel}>
            <X size={16} />
          </button>
        </div>
        <p className="dialog-text">{t("folder.changeText")}</p>
        <FolderField value={folder} onChange={setFolder} onError={setError} autoFocus />
        {error && (
          <p className="field-error" role="alert">
            {error}
          </p>
        )}
        <div className="dialog-actions">
          <button type="button" className="btn" onClick={onCancel}>
            {t("common.cancel")}
          </button>
          <button type="submit" className="btn primary" disabled={working || !folder.trim()}>
            {t("folder.use")}
          </button>
        </div>
      </form>
    </Modal>
  );
}
