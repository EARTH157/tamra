import { type KeyboardEvent, useRef } from "react";
import { useT } from "./i18n";
import SettingsGeneral from "./SettingsGeneral";
import SettingsModel from "./SettingsModel";
import SettingsStyle from "./SettingsStyle";
import type { Collection, IndexStatus, SettingsTab } from "./types";

const TABS: SettingsTab[] = ["general", "model", "style"];

type Props = {
  tab: SettingsTab;
  onTabChange: (tab: SettingsTab) => void;
  collection: Collection | null;
  index: IndexStatus | null;
  chatCount: number;
  /** An answer is being written: the index and the chats cannot be changed now. */
  busy: boolean;
  onChangeFolder: () => void;
  onRebuild: () => void;
  onChatsDeleted: () => Promise<void> | void;
};

/** The Settings page: a heading, three tabs (General, AI model, Style) and the open tab. */
export default function SettingsPage({ tab, onTabChange, ...general }: Props) {
  const t = useT();
  const buttons = useRef<Record<string, HTMLButtonElement | null>>({});

  // Arrow keys move between the tabs, as in the WAI-ARIA tabs pattern.
  function onKeyDown(event: KeyboardEvent) {
    const step = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
    const edge = event.key === "Home" ? 0 : event.key === "End" ? TABS.length - 1 : null;
    if (!step && edge === null) return;
    event.preventDefault();
    const at = TABS.indexOf(tab);
    const next = TABS[edge ?? (at + step + TABS.length) % TABS.length];
    onTabChange(next);
    buttons.current[next]?.focus();
  }

  return (
    <div className="settings">
      <div className="settings-inner">
        <h1>{t("settings.title")}</h1>
        <p className="settings-subtitle">{t(`settings.subtitle.${tab}`)}</p>
        <div className="tabs" role="tablist" aria-label={t("settings.tabs")} onKeyDown={onKeyDown}>
          {TABS.map((name) => (
            <button
              key={name}
              ref={(el) => {
                buttons.current[name] = el;
              }}
              type="button"
              role="tab"
              id={`settings-tab-${name}`}
              aria-selected={tab === name}
              aria-controls="settings-panel"
              tabIndex={tab === name ? 0 : -1}
              className={tab === name ? "tab active" : "tab"}
              onClick={() => onTabChange(name)}
            >
              {t(`settings.tab.${name}`)}
            </button>
          ))}
        </div>
        <div id="settings-panel" role="tabpanel" aria-labelledby={`settings-tab-${tab}`}>
          {tab === "general" && <SettingsGeneral {...general} />}
          {tab === "model" && <SettingsModel />}
          {tab === "style" && <SettingsStyle />}
        </div>
      </div>
    </div>
  );
}
