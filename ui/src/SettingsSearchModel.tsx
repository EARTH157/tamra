import { useEffect, useState } from "react";
import { api, ApiError } from "./api";
import { errorText } from "./apiErrors";
import ConfirmDialog from "./ConfirmDialog";
import { useT } from "./i18n";
import { isDownloading, type ModelsApi } from "./models";
import { Section } from "./SettingsControls";
import { formatSize, ModelAction } from "./SettingsLocal";

const POLL_MS = 1000;
const ROWS = {
  embedding: { title: "settings.searchModel", hint: "settings.searchModelHint", name: "bge-m3" },
  reranker: {
    title: "settings.rerankModel",
    hint: "settings.rerankModelHint",
    name: "bge-reranker-v2-m3",
  },
  ocr: { title: "settings.ocrModel", hint: "settings.ocrModelHint", name: "PP-OCRv5" },
} as const; // the dialog names the model, not its quantisation or file set

type Props = ModelsApi & { kind?: keyof typeof ROWS };

/** A model every mode uses (search, reranking, OCR): status, size and download, above the mode's
 * own list. */
export default function SettingsSearchModel({ models, refresh, kind = "embedding" }: Props) {
  const t = useT();
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const row = ROWS[kind];
  const model = models?.[kind];
  const active = model ? isDownloading(model) : false;

  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => void refresh(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [active, refresh]);

  if (!model) return null;

  async function startDownload() {
    try {
      await api("POST", `/api/models/${model?.id}/download`);
    } catch (e) {
      if (!(e instanceof ApiError) || e.status !== 409) throw e; // shown in the dialog
      setConfirming(false);
      setError(e.message === "Already installed." ? t("settings.alreadyInstalled") : errorText(e, t));
      await refresh();
      return;
    }
    setConfirming(false);
    await refresh();
  }

  async function cancelDownload() {
    setError(null);
    try {
      await api("DELETE", `/api/models/${model?.id}/download`);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <Section title={t(row.title)}>
      {error && (
        <p className="field-error settings-error" role="alert">
          {error}
        </p>
      )}
      <div className="search-model">
        <span className="model-text">
          <span className="model-name">{model.name}</span>
          <span className="model-detail">{formatSize(model.size, t)}</span>
          {model.state === "error" && model.error && (
            <span className="model-error">{t("settings.downloadFailed", { error: model.error })}</span>
          )}
        </span>
        <ModelAction
          model={model}
          onDownload={() => setConfirming(true)}
          onCancel={() => void cancelDownload()}
        />
      </div>
      <p className="settings-row-hint search-model-hint">{t(row.hint)}</p>
      {confirming && (
        <ConfirmDialog
          title={t("settings.downloadTitle", { name: row.name, size: formatSize(model.size, t) })}
          text={t("settings.downloadText")}
          confirmLabel={t("settings.download")}
          onConfirm={startDownload}
          onCancel={() => setConfirming(false)}
        />
      )}
    </Section>
  );
}
