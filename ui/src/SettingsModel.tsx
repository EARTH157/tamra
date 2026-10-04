import { Cloud, Lock } from "lucide-react";
import { useState } from "react";
import { useT } from "./i18n";
import { useSettings } from "./settings";
import SettingsCloud from "./SettingsCloud";
import { RadioMark, Section } from "./SettingsControls";
import SettingsLocal from "./SettingsLocal";
import type { Mode } from "./types";

/** The AI model tab: the mode cards, then the local model list or the Cloud API form. */
export default function SettingsModel() {
  const t = useT();
  const { settings, update } = useSettings();
  const [error, setError] = useState<string | null>(null);

  function choose(mode: Mode) {
    if (mode === settings.mode) return;
    setError(null);
    update({ mode }).catch((e: Error) => setError(e.message));
  }

  const cards = [
    { mode: "local", icon: <Lock size={16} />, title: "settings.modeLocal", text: "settings.modeLocalText" },
    { mode: "api", icon: <Cloud size={16} />, title: "settings.modeCloud", text: "settings.modeCloudText" },
  ] as const;

  return (
    <>
      <Section title={t("settings.aiMode")}>
        {error && (
          <p className="field-error settings-error" role="alert">
            {error}
          </p>
        )}
        <div className="mode-cards" role="radiogroup" aria-label={t("settings.aiMode")}>
          {cards.map((card) => (
            <button
              key={card.mode}
              type="button"
              role="radio"
              aria-checked={settings.mode === card.mode}
              className={settings.mode === card.mode ? "mode-card selected" : "mode-card"}
              onClick={() => choose(card.mode)}
            >
              <span className="mode-icon">{card.icon}</span>
              <span className="mode-text">
                <span className="mode-title">{t(card.title)}</span>
                <span className="mode-detail">{t(card.text)}</span>
              </span>
              <RadioMark on={settings.mode === card.mode} />
            </button>
          ))}
        </div>
      </Section>
      {settings.mode === "api" ? <SettingsCloud /> : <SettingsLocal />}
    </>
  );
}
