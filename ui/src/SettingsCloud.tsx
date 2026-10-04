import { AlertTriangle, Check } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "./api";
import { type TranslationKey, useT } from "./i18n";
import { useSettings } from "./settings";
import { Section, Segmented } from "./SettingsControls";
import type { ApiProvider, ConnectionTest, SettingsChanges } from "./types";

type Status = { kind: "saved" | "connected" | "error"; text: string };

const KEY_DOTS = "•".repeat(20);

const TEST_REASONS: Partial<Record<NonNullable<ConnectionTest["reason"]>, TranslationKey>> = {
  offline: "settings.test.offline",
  auth: "settings.test.auth",
  quota: "settings.test.quota",
  model_missing: "settings.test.modelMissing",
};

/** The Cloud API form: warning, provider, model, base URL, API key, Save and Test connection. */
export default function SettingsCloud() {
  const t = useT();
  const { settings, update, reload } = useSettings();
  const [model, setModel] = useState(settings.api_model);
  const [baseUrl, setBaseUrl] = useState(settings.api_base_url);
  // The key is only ever typed here and sent once; it is never read back, only its hint is shown.
  const [key, setKey] = useState("");
  const [status, setStatus] = useState<Status | null>(null);
  const [working, setWorking] = useState<"save" | "test" | null>(null);
  const anthropic = settings.api_provider === "anthropic";

  // Follow the saved values (after a save, or when the core answers late).
  useEffect(() => setModel(settings.api_model), [settings.api_model]);
  useEffect(() => setBaseUrl(settings.api_base_url), [settings.api_base_url]);

  function chooseProvider(api_provider: ApiProvider) {
    if (api_provider === settings.api_provider) return;
    setStatus(null);
    setKey(""); // a key belongs to its provider
    update({ api_provider }).catch((e: Error) => setStatus({ kind: "error", text: e.message }));
  }

  /** Save the model, the base URL and a typed key. False when something was refused. */
  async function save(): Promise<boolean> {
    const changes: SettingsChanges = {};
    if (model.trim() !== settings.api_model) changes.api_model = model.trim();
    if (!anthropic && baseUrl.trim() !== settings.api_base_url) {
      changes.api_base_url = baseUrl.trim();
    }
    try {
      await update(changes);
      if (key.trim()) {
        await api("PUT", "/api/settings/api-key", { provider: settings.api_provider, key });
        setKey("");
        await reload();
      }
      return true;
    } catch (e) {
      setStatus({ kind: "error", text: (e as Error).message });
      return false;
    }
  }

  async function onSave() {
    setWorking("save");
    setStatus(null);
    if (await save()) setStatus({ kind: "saved", text: t("settings.saved") });
    setWorking(null);
  }

  // The core tests what is saved, so whatever is typed is saved first.
  async function onTest() {
    setWorking("test");
    setStatus(null);
    try {
      if (!(await save())) return;
      const result = await api<ConnectionTest>("POST", "/api/settings/test-connection");
      const reason = result.reason ? TEST_REASONS[result.reason] : undefined;
      setStatus(
        result.ok
          ? { kind: "connected", text: t("settings.connected") }
          : { kind: "error", text: reason ? t(reason) : result.message },
      );
    } catch (e) {
      setStatus({ kind: "error", text: (e as Error).message });
    } finally {
      setWorking(null);
    }
  }

  const keyPlaceholder = settings.api_key_set
    ? `${KEY_DOTS}${settings.api_key_hint ?? ""}`
    : t("settings.apiKeyPlaceholder");

  return (
    <>
      <p className="warning-note">
        <AlertTriangle size={16} />
        {t("settings.cloudWarning")}
      </p>
      <Section title={t("settings.provider")}>
        <Segmented
          value={settings.api_provider}
          label={t("settings.provider")}
          options={[
            { value: "anthropic", label: t("settings.providerAnthropic") },
            { value: "openai", label: t("settings.providerOpenai") },
          ]}
          onChange={chooseProvider}
        />
        <div className="field-grid">
          <label className="field">
            <span className="field-label">{t("settings.apiModel")}</span>
            <input
              className="field-input"
              value={model}
              spellCheck={false}
              onChange={(e) => setModel(e.target.value)}
            />
          </label>
          <label className="field">
            <span className="field-label">{t("settings.baseUrl")}</span>
            <input
              className="field-input"
              value={anthropic ? "" : baseUrl}
              placeholder={t("settings.baseUrlPlaceholder")}
              disabled={anthropic}
              spellCheck={false}
              onChange={(e) => setBaseUrl(e.target.value)}
            />
          </label>
          <label className={settings.api_key_set ? "field wide key-set" : "field wide"}>
            <span className="field-label">{t("settings.apiKey")}</span>
            <input
              className="field-input"
              type="password"
              autoComplete="off"
              spellCheck={false}
              value={key}
              placeholder={keyPlaceholder}
              onChange={(e) => setKey(e.target.value)}
            />
          </label>
        </div>
        <p className="field-note">{t("settings.apiKeyNote")}</p>
        <p className="field-note">{t("settings.testHint")}</p>
        <div className="cloud-actions">
          <button
            type="button"
            className="btn primary"
            onClick={() => void onSave()}
            disabled={working !== null || !model.trim()}
          >
            {t("settings.save")}
          </button>
          <button
            type="button"
            className="btn"
            onClick={() => void onTest()}
            disabled={working !== null || !model.trim()}
          >
            {working === "test" ? t("settings.testing") : t("settings.test")}
          </button>
          {status && (
            <span
              className={status.kind === "error" ? "test-result bad" : "test-result good"}
              role={status.kind === "error" ? "alert" : "status"}
            >
              {status.kind === "error" ? <AlertTriangle size={14} /> : <Check size={14} />}
              {status.text}
            </span>
          )}
        </div>
      </Section>
    </>
  );
}
