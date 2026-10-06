import { Folder, X } from "lucide-react";
import { type FormEvent, useState } from "react";
import { projectErrorText } from "./apiErrors";
import FolderField from "./FolderField";
import { useT } from "./i18n";
import { IndexBody } from "./IndexStatus";
import Modal from "./Modal";
import type { Project } from "./types";

const NAME_MAX = 80; // the core refuses longer names

type NewProps = {
  /** Create the project: resolve to close the dialog; reject with an ApiError to show why it was refused. */
  onCreate: (name: string, folder: string) => Promise<void>;
  onCancel: () => void;
};

/** The New project dialog: a name (optional: the folder's name is used) and a folder of documents. */
export function NewProjectDialog({ onCreate, onCancel }: NewProps) {
  const t = useT();
  const [name, setName] = useState("");
  const [folder, setFolder] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [working, setWorking] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setWorking(true);
    setError(null);
    try {
      await onCreate(name.trim(), folder.trim());
    } catch (e) {
      setError(projectErrorText(e as Error, t));
      setWorking(false);
    }
  }

  return (
    <Modal labelledBy="new-project-title" onClose={onCancel}>
      <form onSubmit={submit}>
        <div className="dialog-header">
          <h2 id="new-project-title">{t("project.new.title")}</h2>
          <button type="button" className="icon-button" aria-label={t("common.close")} onClick={onCancel}>
            <X size={16} />
          </button>
        </div>
        <p className="dialog-text">{t("project.new.text")}</p>
        <label className="field">
          <span className="field-label">{t("project.nameLabel")}</span>
          <input
            className="field-input"
            value={name}
            maxLength={NAME_MAX}
            placeholder={t("project.new.namePlaceholder")}
            onChange={(e) => setName(e.target.value)}
            data-autofocus
          />
        </label>
        <div className="field">
          <span className="field-label">{t("project.folderLabel")}</span>
          <FolderField value={folder} onChange={setFolder} onError={setError} />
          <span className="field-hint">{t("project.new.hint")}</span>
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
            {t("project.new.create")}
          </button>
        </div>
      </form>
    </Modal>
  );
}

type SettingsProps = {
  /** The project as the core last reported it: the index status follows the polling. */
  project: Project;
  /** An answer is being written: the folder and the index cannot change now. */
  busy: boolean;
  /** Rename the project: resolve to close the dialog; reject with an ApiError to show why it was refused. */
  onRename: (name: string) => Promise<void>;
  onChangeFolder: () => void;
  onRebuild: () => Promise<void>;
  onClose: () => void;
};

/** Project settings: its name, its folder, and its search index (status and Rebuild index). */
export function ProjectSettingsDialog({
  project,
  busy,
  onRename,
  onChangeFolder,
  onRebuild,
  onClose,
}: SettingsProps) {
  const t = useT();
  const [name, setName] = useState(project.name);
  const [error, setError] = useState<string | null>(null);
  const [working, setWorking] = useState(false);
  const next = name.trim();

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!next || next === project.name) return;
    setWorking(true);
    setError(null);
    try {
      await onRename(next);
    } catch (e) {
      setError(projectErrorText(e as Error, t));
      setWorking(false);
    }
  }

  async function rebuild() {
    setError(null);
    try {
      await onRebuild();
    } catch (e) {
      setError(projectErrorText(e as Error, t));
    }
  }

  return (
    <Modal labelledBy="project-settings-title" onClose={onClose}>
      <form onSubmit={save}>
        <div className="dialog-header">
          <h2 id="project-settings-title">{t("project.settings")}</h2>
          <button type="button" className="icon-button" aria-label={t("common.close")} onClick={onClose}>
            <X size={16} />
          </button>
        </div>
        <p className="dialog-text">{t("project.settings.text")}</p>
        <label className="field">
          <span className="field-label">{t("project.nameLabel")}</span>
          <input
            className="field-input"
            value={name}
            maxLength={NAME_MAX}
            onChange={(e) => setName(e.target.value)}
            data-autofocus
          />
        </label>
        <div className="field">
          <span className="field-label">{t("project.folderLabel")}</span>
          <div className="path-row">
            <div className="path-field" title={project.folder_path}>
              <Folder size={16} />
              <span className="path-text">{project.folder_path}</span>
            </div>
            <button type="button" className="btn" onClick={onChangeFolder} disabled={busy}>
              {t("index.changeFolder")}
            </button>
          </div>
        </div>
        <div className="field">
          <span className="field-label">{t("project.settings.index")}</span>
          <div className="index-panel">
            <IndexBody index={project.index} />
          </div>
          <div>
            <button type="button" className="btn" onClick={() => void rebuild()} disabled={busy}>
              {t("index.rebuild")}
            </button>
          </div>
        </div>
        {error && (
          <p className="field-error" role="alert">
            {error}
          </p>
        )}
        <div className="dialog-actions">
          <button type="button" className="btn" onClick={onClose}>
            {t("common.close")}
          </button>
          <button
            type="submit"
            className="btn primary"
            disabled={working || !next || next === project.name}
          >
            {t("project.settings.save")}
          </button>
        </div>
      </form>
    </Modal>
  );
}
