import { useState } from "react";
import { api, ApiError } from "./api";
import ConfirmDialog from "./ConfirmDialog";
import { useT } from "./i18n";
import { useSettings } from "./settings";
import { Row, Section, Select, Toggle } from "./SettingsControls";
import type { Collection, IndexStatus } from "./types";

type Props = {
  collection: Collection | null;
  index: IndexStatus | null;
  chatCount: number;
  /** An answer is being written: the index cannot be rebuilt now. */
  busy: boolean;
  onChangeFolder: () => void;
  onRebuild: () => void;
  /** All chats were deleted: the app reloads its list and leaves any open chat. */
  onChatsDeleted: () => Promise<void> | void;
};

const DEFAULT_DATA_FOLDER = "%LOCALAPPDATA%\\Tamra"; // until the core reports the real path

/** The General tab: language, deleting chats, the documents folder and index, and the data. */
export default function SettingsGeneral({
  collection,
  index,
  chatCount,
  busy,
  onChangeFolder,
  onRebuild,
  onChatsDeleted,
}: Props) {
  const t = useT();
  const { settings, update } = useSettings();
  const [error, setError] = useState<string | null>(null);
  // Opening the data folder is refused outside the Tamra window (the dev server): the button is
  // then replaced by a note.
  const [canOpenData, setCanOpenData] = useState(true);
  const [confirmingDeleteAll, setConfirmingDeleteAll] = useState(false);

  function save(changes: Parameters<typeof update>[0]) {
    setError(null);
    update(changes).catch((e: Error) => setError(e.message));
  }

  async function openDataFolder() {
    setError(null);
    try {
      await api("POST", "/api/open-data-folder");
    } catch (e) {
      if (e instanceof ApiError && e.status === 501) setCanOpenData(false);
      else setError((e as Error).message);
    }
  }

  async function deleteAll() {
    await api("DELETE", "/api/chats");
    setConfirmingDeleteAll(false);
    await onChatsDeleted();
  }

  function indexSummary(): string {
    const counts = index?.counts;
    if (!counts) return t("index.checking");
    const total = Object.values(counts).reduce((sum, n) => sum + n, 0);
    if (index?.stale) return t("index.stale");
    if (total === 0) return t("settings.indexNone");
    if (counts.indexed === total) return t("settings.indexSummary", { count: total });
    return t("settings.indexPartial", { indexed: counts.indexed, total });
  }

  return (
    <>
      {error && (
        <p className="field-error settings-error" role="alert">
          {error}
        </p>
      )}
      <Section title={t("settings.general")}>
        <div className="card">
          <Row title={t("settings.language")} hint={t("settings.languageHint")}>
            <Select
              label={t("settings.language")}
              value={settings.language}
              options={[
                { value: "en", label: "English" },
                { value: "th", label: "ไทย" },
              ]}
              onChange={(language) => save({ language })}
            />
          </Row>
          <Row title={t("settings.askBeforeDelete")}>
            <Toggle
              label={t("settings.askBeforeDelete")}
              checked={settings.ask_before_delete}
              onChange={(ask_before_delete) => save({ ask_before_delete })}
            />
          </Row>
        </div>
      </Section>

      <Section title={t("settings.documents")}>
        <div className="card">
          <Row
            title={t("settings.docFolder")}
            hint={collection ? collection.folder_path : t("settings.noFolder")}
          >
            <button type="button" className="btn" onClick={onChangeFolder} disabled={busy}>
              {collection ? t("settings.changeFolder") : t("settings.chooseFolder")}
            </button>
          </Row>
          {collection && (
            <Row title={t("settings.searchIndex")} hint={indexSummary()}>
              <button type="button" className="btn" onClick={onRebuild} disabled={busy}>
                {t("settings.rebuild")}
              </button>
            </Row>
          )}
        </div>
      </Section>

      <Section title={t("settings.data")}>
        <div className="card">
          <Row title={t("settings.dataFolder")} hint={settings.data_dir || DEFAULT_DATA_FOLDER}>
            {canOpenData ? (
              <button type="button" className="btn" onClick={() => void openDataFolder()}>
                {t("settings.openFolder")}
              </button>
            ) : (
              <span className="settings-row-hint">{t("settings.openFolderUnavailable")}</span>
            )}
          </Row>
          <Row
            title={t("settings.chatHistory")}
            hint={
              chatCount === 0
                ? t("settings.chatCountNone")
                : t("settings.chatCount", { count: chatCount })
            }
          >
            <button
              type="button"
              className="btn danger-outline"
              onClick={() => setConfirmingDeleteAll(true)}
              disabled={chatCount === 0 || busy}
            >
              {t("settings.deleteAll")}
            </button>
          </Row>
        </div>
      </Section>

      <p className="settings-footer">{t("settings.footer")}</p>

      {confirmingDeleteAll && (
        <ConfirmDialog
          title={t("settings.deleteAllTitle")}
          text={t("settings.deleteAllText", { count: chatCount })}
          confirmLabel={t("settings.deleteAll")}
          danger
          onConfirm={deleteAll}
          onCancel={() => setConfirmingDeleteAll(false)}
        />
      )}
    </>
  );
}
