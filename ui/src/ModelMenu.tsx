import { Check, ChevronDown, Cloud, Cpu, SlidersHorizontal } from "lucide-react";
import { type ReactNode, useEffect, useRef, useState } from "react";
import { useT } from "./i18n";
import { activeName, isDownloading, offeredLocal, percent } from "./models";
import PopMenu from "./PopMenu";
import type { LocalModel, ModelsInfo, Settings, SettingsChanges } from "./types";

const POLL_MS = 1000;

type Props = {
  models: ModelsInfo | null;
  settings: Settings;
  /** Save the choice: mode and local_model_id. */
  onChoose: (changes: SettingsChanges) => void;
  onManage: () => void;
  refresh: () => Promise<void>;
};

const PROVIDER_NAMES = { anthropic: "Anthropic", openai: "OpenAI" } as const;

/** The model chip and its menu: local models, the cloud model, and a way to Settings. */
export default function ModelMenu({ models, settings, onChoose, onManage, refresh }: Props) {
  const t = useT();
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const downloading = useRef(false);
  downloading.current = models?.local.some(isDownloading) ?? false;

  // While the menu is open: fresh data now, and progress every second as long as a model downloads.
  const open = anchor !== null;
  useEffect(() => {
    if (!open) return;
    void refresh();
    const timer = window.setInterval(() => {
      if (downloading.current) void refresh();
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [open, refresh]);

  const api = settings.mode === "api";
  const name = activeName(models, settings) ?? t("model.none");
  const provider = PROVIDER_NAMES[settings.api_provider];
  const local = models ? offeredLocal(models) : [];
  const imported = models?.uncatalogued ?? [];
  // In API mode the cloud entry is the active one, whatever local model would be used.
  const activeId = api ? null : (models?.active.id ?? null);

  function describe(model: LocalModel): string {
    const parts: string[] = [t(`model.tier.${model.tier}`)];
    if (model.state === "downloading") parts.push(t("model.downloading", { percent: percent(model) }));
    else if (model.state === "verifying") parts.push(t("model.verifying"));
    if (model.thinking) parts.push(t("model.canThink"));
    return parts.join(" · ");
  }

  function choose(changes: SettingsChanges) {
    if (anchor) {
      setAnchor(null);
      anchor.focus();
    }
    onChoose(changes);
  }

  return (
    <>
      <button
        type="button"
        className={open ? "chip open" : "chip"}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`${t("model.chip")}: ${name}`}
        onClick={(event) => setAnchor(anchor ? null : event.currentTarget)}
      >
        {api ? <Cloud size={14} /> : <Cpu size={14} />}
        {name}
        <ChevronDown size={13} className="chip-chevron" />
      </button>
      {anchor && (
        <PopMenu
          anchor={anchor}
          label={t("model.menu")}
          className="wider"
          onClose={(refocus) => {
            setAnchor(null);
            if (refocus) anchor.focus();
          }}
        >
          <div className="menu-heading">{t("model.onThisComputer")}</div>
          {models && local.length === 0 && imported.length === 0 && (
            <p className="menu-empty">{t("model.noneInstalled")}</p>
          )}
          {local.map((model) => (
            <Entry
              key={model.id}
              icon={<Cpu size={16} />}
              name={model.name}
              note={describe(model)}
              checked={model.id === activeId}
              disabled={isDownloading(model)}
              onChoose={() => choose({ mode: "local", local_model_id: model.id })}
            />
          ))}
          {imported.map((model) => (
            <Entry
              key={model.id}
              icon={<Cpu size={16} />}
              name={model.name}
              note={t("model.imported")}
              checked={model.id === activeId}
              onChoose={() => choose({ mode: "local", local_model_id: model.id })}
            />
          ))}
          <div className="menu-heading">{t("model.cloud")}</div>
          <Entry
            icon={<Cloud size={16} />}
            name={settings.api_model}
            note={t(settings.api_key_set ? "model.cloudSent" : "model.cloudNoKey", { provider })}
            checked={api}
            disabled={!settings.api_key_set && !api}
            onChoose={() => choose({ mode: "api" })}
          />
          <div className="menu-divider" role="separator" />
          <button
            type="button"
            role="menuitem"
            className="menu-item"
            onClick={() => {
              setAnchor(null);
              onManage();
            }}
          >
            <SlidersHorizontal size={16} />
            {t("model.manage")}
          </button>
        </PopMenu>
      )}
    </>
  );
}

type EntryProps = {
  icon: ReactNode;
  name: string;
  note: string;
  checked: boolean;
  disabled?: boolean;
  onChoose: () => void;
};

function Entry({ icon, name, note, checked, disabled, onChoose }: EntryProps) {
  return (
    <button
      type="button"
      role="menuitemradio"
      aria-checked={checked}
      className="menu-entry"
      disabled={disabled}
      onClick={onChoose}
    >
      <span className="entry-icon">{icon}</span>
      <span className="entry-text">
        <span className="entry-name">{name}</span>
        <span className="entry-note">{note}</span>
      </span>
      {checked && <Check size={16} className="entry-check" />}
    </button>
  );
}
