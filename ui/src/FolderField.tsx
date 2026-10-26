import { Folder } from "lucide-react";
import { api } from "./api";
import { useT } from "./i18n";
import { useSettings } from "./settings";

type Props = {
  value: string;
  onChange: (folder: string) => void;
  /** The folder picker failed (or null: clear the last error). */
  onError: (message: string | null) => void;
  /** Focus the path when the dialog opens. */
  autoFocus?: boolean;
};

/** A folder path you can type, with a Browse… button for the system's folder picker. */
export default function FolderField({ value, onChange, onError, autoFocus }: Props) {
  const t = useT();
  const { settings } = useSettings();

  async function browse() {
    onError(null);
    try {
      const picked = await api<{ folder_path: string | null }>("POST", "/api/pick-folder");
      if (picked.folder_path) onChange(picked.folder_path);
    } catch (e) {
      onError((e as Error).message);
    }
  }

  return (
    <div className="path-row">
      <label className="path-field">
        <Folder size={16} />
        <input
          aria-label={t("folder.pathLabel")}
          placeholder={t(
            settings.platform === "linux" ? "folder.pathPlaceholder.linux" : "folder.pathPlaceholder",
          )}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          data-autofocus={autoFocus ? "" : undefined}
        />
      </label>
      <button type="button" className="btn" onClick={() => void browse()}>
        {t("folder.browse")}
      </button>
    </div>
  );
}
