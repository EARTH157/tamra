import { useState } from "react";
import { useT } from "./i18n";
import { useSettings } from "./settings";
import { RadioMark, Row, Section, Segmented } from "./SettingsControls";
import type { Accent, SettingsChanges, ThemeSetting } from "./types";

const THEMES: ThemeSetting[] = ["light", "dark", "system"];
const ACCENTS: Accent[] = ["green", "blue", "orange", "purple", "slate"];

/** One half of a theme preview: a miniature of the app in the light or the dark palette. */
function Half({ look, sidebar }: { look: "light" | "dark"; sidebar: boolean }) {
  return (
    <span className="tp-half" data-look={look}>
      {sidebar && <span className="tp-side" />}
      <span className="tp-body">
        <span className="tp-chip" />
        <span className="tp-line long" />
        <span className="tp-line" />
      </span>
    </span>
  );
}

/** The Style tab: theme, accent colour, text size, spacing, and a live preview. */
export default function SettingsStyle() {
  const t = useT();
  const { settings, update } = useSettings();
  const [error, setError] = useState<string | null>(null);

  function save(changes: SettingsChanges) {
    setError(null);
    update(changes).catch((e: Error) => setError(e.message));
  }

  return (
    <>
      {error && (
        <p className="field-error settings-error" role="alert">
          {error}
        </p>
      )}
      <Section title={t("settings.theme")}>
        <div className="theme-cards" role="radiogroup" aria-label={t("settings.theme")}>
          {THEMES.map((theme) => (
            <button
              key={theme}
              type="button"
              role="radio"
              aria-checked={settings.theme === theme}
              className={settings.theme === theme ? "theme-card selected" : "theme-card"}
              onClick={() => save({ theme })}
            >
              <span className="theme-preview" aria-hidden="true">
                {theme === "system" ? (
                  <>
                    <Half look="light" sidebar />
                    <Half look="dark" sidebar={false} />
                  </>
                ) : (
                  <Half look={theme} sidebar />
                )}
              </span>
              <span className="theme-name">
                {t(`settings.theme.${theme}`)}
                <RadioMark on={settings.theme === theme} />
              </span>
            </button>
          ))}
        </div>
      </Section>

      <Section title={t("settings.colourText")}>
        <div className="card">
          <Row title={t("settings.accent")} hint={t("settings.accentHint")}>
            <div className="swatches" role="radiogroup" aria-label={t("settings.accent")}>
              {ACCENTS.map((accent) => (
                <button
                  key={accent}
                  type="button"
                  role="radio"
                  aria-checked={settings.accent === accent}
                  aria-label={t(`settings.accent.${accent}`)}
                  title={t(`settings.accent.${accent}`)}
                  className="swatch"
                  data-swatch={accent}
                  onClick={() => save({ accent })}
                />
              ))}
            </div>
          </Row>
          <Row title={t("settings.textSize")} hint={t("settings.textSizeHint")}>
            <Segmented
              value={settings.text_size}
              label={t("settings.textSize")}
              options={[
                { value: "small", label: t("settings.size.small") },
                { value: "default", label: t("settings.size.default") },
                { value: "large", label: t("settings.size.large") },
              ]}
              onChange={(text_size) => save({ text_size })}
            />
          </Row>
          <Row title={t("settings.spacing")}>
            <Segmented
              value={settings.spacing}
              label={t("settings.spacing")}
              options={[
                { value: "comfortable", label: t("settings.spacing.comfortable") },
                { value: "compact", label: t("settings.spacing.compact") },
              ]}
              onChange={(spacing) => save({ spacing })}
            />
          </Row>
        </div>
      </Section>

      <Section title={t("settings.preview")}>
        <div className="preview">
          <div className="message user">{t("settings.previewQuestion")}</div>
          <div className="answer-text">
            {t("settings.previewAnswer")}
            <span className="cite">1</span>
          </div>
        </div>
      </Section>
    </>
  );
}
