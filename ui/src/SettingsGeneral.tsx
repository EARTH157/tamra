import { AlertTriangle } from "lucide-react";
import { useState } from "react";
import { api, ApiError } from "./api";
import ConfirmDialog from "./ConfirmDialog";
import { useT } from "./i18n";
import { useSettings } from "./settings";
import { Row, Section, Select, Toggle } from "./SettingsControls";

type Props = {
  chatCount: number;
  /** An answer is being written: the chats cannot be deleted now. */
  busy: boolean;
  /** All chats were deleted: the app reloads its list and leaves any open chat. */
  onChatsDeleted: () => Promise<void> | void;
};

// Shown until the core reports the real path.
const DEFAULT_DATA_FOLDER = { windows: "%LOCALAPPDATA%\\Tamra", linux: "~/.local/share/tamra" };

/** The JSON an MCP app's config takes to start Tamra's MCP server (an `mcpServers` entry). */
function mcpSetup(command: string[]): string {
  const [exe, ...args] = command;
  return JSON.stringify({ mcpServers: { tamra: { command: exe, args } } }, null, 2);
}

/** The General tab: language, deleting chats, AI apps (MCP), and the data Tamra keeps. */
export default function SettingsGeneral({ chatCount, busy, onChatsDeleted }: Props) {
  const t = useT();
  const { settings, update } = useSettings();
  const [error, setError] = useState<string | null>(null);
  // Opening the data folder is refused outside the Tamra window (the dev server): the button is
  // then replaced by a note.
  const [canOpenData, setCanOpenData] = useState(true);
  const [confirmingDeleteAll, setConfirmingDeleteAll] = useState(false);
  const [copied, setCopied] = useState(false);
  const [manualSetup, setManualSetup] = useState<string | null>(null);

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

  async function copySetup() {
    setError(null);
    const setup = mcpSetup(settings.mcp_command);
    try {
      await navigator.clipboard.writeText(setup);
      setCopied(true);
    } catch {
      // Some web views refuse clipboard writes: show the text so it can be copied by hand.
      setError(t("settings.mcpCopyFailed"));
      setManualSetup(setup);
    }
  }

  async function deleteAll() {
    await api("DELETE", "/api/chats");
    setConfirmingDeleteAll(false);
    await onChatsDeleted();
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

      <Section title={t("settings.mcp")}>
        <p className="warning-note">
          <AlertTriangle size={16} />
          {t("settings.mcpWarning")}
        </p>
        <div className="card">
          <Row title={t("settings.mcpEnable")} hint={t("settings.mcpHint")}>
            <Toggle
              label={t("settings.mcpEnable")}
              checked={settings.mcp_enabled}
              onChange={(mcp_enabled) => save({ mcp_enabled })}
            />
          </Row>
          <Row title={t("settings.mcpSetup")} hint={t("settings.mcpSetupHint")}>
            <button
              type="button"
              className="btn"
              disabled={settings.mcp_command.length === 0}
              onClick={() => void copySetup()}
            >
              {copied ? t("settings.mcpCopied") : t("settings.mcpCopy")}
            </button>
          </Row>
        </div>
        {manualSetup && (
          <textarea
            className="mcp-setup"
            readOnly
            rows={8}
            value={manualSetup}
            aria-label={t("settings.mcpSetup")}
            onFocus={(e) => e.currentTarget.select()}
          />
        )}
      </Section>

      <Section title={t("settings.data")}>
        <div className="card">
          <Row title={t("settings.dataFolder")} hint={settings.data_dir || DEFAULT_DATA_FOLDER[settings.platform]}>
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
