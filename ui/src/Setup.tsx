import { Folder, X } from "lucide-react";
import { type FormEvent, useState } from "react";
import { api } from "./api";
import { useT } from "./i18n";
import Modal from "./Modal";
import type { CollectionState } from "./types";

type Props = {
  onDone: (state: CollectionState) => void;
  onCancel: () => void;
  title?: string;
  description?: string;
  initialFolder?: string;
};

/** The folder dialog: choose the folder of documents to index. */
export default function Setup({
  onDone,
  onCancel,
  title,
  description,
  initialFolder = "",
}: Props) {
  const t = useT();
  const [folder, setFolder] = useState(initialFolder);
  const [error, setError] = useState<string | null>(null);
  const [working, setWorking] = useState(false);

  async function browse() {
    setError(null);
    try {
      const picked = await api<{ folder_path: string | null }>("POST", "/api/pick-folder");
      if (picked.folder_path) setFolder(picked.folder_path);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setWorking(true);
    setError(null);
    try {
      const state = await api<CollectionState>("PUT", "/api/collection", {
        name: "",
        folder_path: folder.trim(),
      });
      onDone(state);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setWorking(false);
    }
  }

  return (
    <Modal labelledBy="folder-dialog-title" onClose={onCancel}>
      <form onSubmit={submit}>
        <div className="dialog-header">
          <h2 id="folder-dialog-title">{title ?? t("folder.changeTitle")}</h2>
          <button type="button" className="icon-button" aria-label={t("common.close")} onClick={onCancel}>
            <X size={16} />
          </button>
        </div>
        <p className="dialog-text">{description ?? t("folder.changeText")}</p>
        <div className="path-row">
          <label className="path-field">
            <Folder size={16} />
            <input
              aria-label={t("folder.pathLabel")}
              placeholder={t("folder.pathPlaceholder")}
              value={folder}
              onChange={(e) => setFolder(e.target.value)}
              data-autofocus
            />
          </label>
          <button type="button" className="btn" onClick={browse}>
            {t("folder.browse")}
          </button>
        </div>
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
