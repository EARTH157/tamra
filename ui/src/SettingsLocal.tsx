import { AlertTriangle, Check, Info } from "lucide-react";
import { type FormEvent, useEffect, useState } from "react";
import { api, ApiError } from "./api";
import { errorText } from "./apiErrors";
import ConfirmDialog from "./ConfirmDialog";
import { type TranslationKey, useT } from "./i18n";
import { isDownloading, type ModelsApi, percent, SOURCE_KEYS } from "./models";
import { useSettings } from "./settings";
import { RadioMark, Row, Section, Select } from "./SettingsControls";
import type {
  Gpu,
  ImportResult,
  LocalContext,
  LocalModel,
  ModelsInfo,
  ModelStatus,
  UncataloguedModel,
} from "./types";

const POLL_MS = 1000;

type Translate = (key: TranslationKey, params?: Record<string, string | number>) => string;

/** "2.5 GB", or "30 MB" for a smaller file. */
export function formatSize(bytes: number, t: Translate): string {
  if (bytes >= 1e9) return t("settings.sizeGb", { size: (bytes / 1e9).toFixed(1) });
  return t("settings.sizeMb", { size: Math.max(1, Math.round(bytes / 1e6)) });
}

/**
 * The GPU that runs the model: the discrete one with the most memory. An integrated GPU only
 * counts when there is no discrete one (it reports shared system memory, often more than a card).
 */
export function bestGpu(gpus: Gpu[]): Gpu | null {
  const most = (list: Gpu[]) =>
    list.reduce<Gpu | null>(
      (top, gpu) => (top === null || gpu.vram_mb > top.vram_mb ? gpu : top),
      null,
    );
  return most(gpus.filter((gpu) => !gpu.integrated)) ?? most(gpus);
}

/** "Detected 16 GB RAM · NVIDIA RTX 3060, 12 GB VRAM". */
function detected(hardware: ModelsInfo["hardware"], t: Translate): string {
  const best = bestGpu(hardware.gpus);
  const gpu = best
    ? t("settings.gpu", { name: best.name, vram: Math.round(best.vram_mb / 1024) })
    : t("settings.noGpu");
  return t("settings.detected", { ram: Math.round(hardware.ram_gb), gpu });
}

/** "8k", "16k", "41k": a context size as the Settings page names it. */
export function contextSize(tokens: number): string {
  return `${Math.round(tokens / 1024)}k`;
}

const CONTEXT_SIZES: LocalContext[] = ["8192", "16384", "32768", "65536"];

/** The context window choice, with what "auto" picks here and why a choice may not apply. */
export function ContextSetting({ context }: { context: NonNullable<ModelsInfo["context"]> }) {
  const t = useT();
  const { settings, update } = useSettings();
  const [error, setError] = useState<string | null>(null);
  const chosen = settings.local_context;
  const options = [
    {
      value: "auto" as LocalContext,
      label: t("settings.contextAuto", { size: contextSize(context.auto) }),
    },
    ...CONTEXT_SIZES.map((value) => ({
      value,
      label: t("settings.contextTokens", { size: contextSize(Number(value)) }),
    })),
  ];
  const capped = chosen !== "auto" && context.tokens < Number(chosen);
  const tooBig = chosen !== "auto" && context.tokens > context.auto;
  return (
    <Section title={t("settings.contextSection")}>
      <div className="card">
        <Row title={t("settings.context")} hint={t("settings.contextHint")}>
          <Select
            label={t("settings.context")}
            value={chosen}
            options={options}
            onChange={(local_context) => {
              setError(null);
              update({ local_context }).catch((e: Error) => setError(e.message));
            }}
          />
        </Row>
      </div>
      {capped && (
        <p className="settings-cpu-note" role="status">
          <Info size={14} />
          {t("settings.contextCapped", { size: contextSize(context.tokens) })}
        </p>
      )}
      {tooBig && (
        <p className="settings-cpu-note" role="status">
          <AlertTriangle size={14} />
          {t("settings.contextTooBig", { size: contextSize(context.auto) })}
        </p>
      )}
      {error && (
        <p className="field-error settings-error" role="alert">
          {error}
        </p>
      )}
    </Section>
  );
}

function baseName(path: string): string {
  return path.split(/[\\/]/).pop() ?? path;
}

type ImportNote = { ok: boolean; text: string };

/** The local model list: pick one, download or cancel, and import a file. */
export default function SettingsLocal({ models, refresh }: ModelsApi) {
  const t = useT();
  const { settings, update } = useSettings();
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<LocalModel | null>(null);
  const [typing, setTyping] = useState(false); // no file picker here: ask for the path instead
  const [path, setPath] = useState("");
  const [importing, setImporting] = useState(false);
  const [note, setNote] = useState<ImportNote | null>(null);

  // Progress, once a second, only while a model downloads or is being checked.
  const active = models?.local.some(isDownloading) ?? false;
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => void refresh(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [active, refresh]);

  function select(id: string) {
    if (id === models?.active.id) return;
    setError(null);
    // Refresh once the core has answered, so the radio never shows an older active model.
    update({ local_model_id: id })
      .then(refresh)
      .catch((e: Error) => setError(e.message));
  }

  async function startDownload(model: LocalModel) {
    try {
      await api("POST", `/api/models/${model.id}/download`);
    } catch (e) {
      if (!(e instanceof ApiError) || e.status !== 409) throw e; // shown in the dialog
      // Already installed, or another download is running: the list was out of date.
      setConfirming(null);
      setError(e.message === "Already installed." ? t("settings.alreadyInstalled") : errorText(e, t));
      await refresh();
      return;
    }
    setConfirming(null);
    await refresh();
  }

  async function cancelDownload(model: LocalModel) {
    setError(null);
    try {
      await api("DELETE", `/api/models/${model.id}/download`);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function unlink(model: UncataloguedModel) {
    setError(null);
    try {
      await api("POST", "/api/models/unlink", { path: model.path });
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function importPath(file: string) {
    setImporting(true);
    setNote(null);
    try {
      const result = await api<ImportResult>("POST", "/api/models/import", { path: file });
      const name = baseName(result.path);
      const warning = t("settings.uncatalogued");
      setNote({
        ok: true,
        text: result.linked
          ? result.catalogued
            ? t("settings.importedLinked", { file: name })
            : t("settings.importedLinkedOther", { file: name, warning })
          : result.catalogued
            ? t("settings.importedCatalogued", { file: name })
            : t("settings.importedOther", { file: name, warning }),
      });
      setTyping(false);
      setPath("");
      await refresh();
    } catch (e) {
      setNote({ ok: false, text: (e as Error).message });
    } finally {
      setImporting(false);
    }
  }

  async function chooseFile() {
    setNote(null);
    try {
      const picked = await api<{ file_path: string | null }>("POST", "/api/pick-file");
      if (picked.file_path) await importPath(picked.file_path);
    } catch (e) {
      if (e instanceof ApiError && e.status === 501) setTyping(true);
      else setNote({ ok: false, text: (e as Error).message });
    }
  }

  function submitPath(event: FormEvent) {
    event.preventDefault();
    if (path.trim()) void importPath(path.trim());
  }

  return (
    <Section title={t("settings.localModel")}>
      {models && <p className="settings-detected">{detected(models.hardware, t)}</p>}
      {models?.gpu_offload === false && settings.mode === "local" && (
        <p className="settings-cpu-note" role="status">
          <Info size={14} />
          {t("model.cpuFallback")}
        </p>
      )}
      {error && (
        <p className="field-error settings-error" role="alert">
          {error}
        </p>
      )}
      <div className="model-list" role="radiogroup" aria-label={t("settings.localModel")}>
        {models?.local.map((model) => (
          <ModelRow
            key={model.id}
            model={model}
            selected={model.id === models.active.id}
            onSelect={() => select(model.id)}
            onDownload={() => setConfirming(model)}
            onCancel={() => void cancelDownload(model)}
          />
        ))}
        {models?.uncatalogued.map((model) => (
          <ImportedRow
            key={model.id}
            model={model}
            selected={model.id === models.active.id}
            onSelect={() => select(model.id)}
            onUnlink={() => void unlink(model)}
          />
        ))}
      </div>

      <div className="import-row">
        <button
          type="button"
          className="btn"
          onClick={() => void chooseFile()}
          disabled={importing}
        >
          {importing ? t("settings.importing") : t("settings.importButton")}
        </button>
        <span className="settings-row-hint">{t("settings.importHint")}</span>
      </div>
      {typing && (
        <form className="path-row import-path" onSubmit={submitPath}>
          <label className="path-field">
            <input
              aria-label={t("settings.importPathLabel")}
              placeholder={t(
                settings.platform === "linux"
                  ? "settings.importPathPlaceholder.linux"
                  : "settings.importPathPlaceholder",
              )}
              value={path}
              onChange={(e) => setPath(e.target.value)}
              data-autofocus
            />
          </label>
          <button type="submit" className="btn primary" disabled={importing || !path.trim()}>
            {t("settings.importGo")}
          </button>
        </form>
      )}
      {note && (
        <p className={note.ok ? "import-note" : "field-error"} role={note.ok ? "status" : "alert"}>
          {note.text}
        </p>
      )}

      {confirming && (
        <ConfirmDialog
          title={t("settings.downloadTitle", {
            name: confirming.name,
            size: formatSize(confirming.size, t),
          })}
          text={t("settings.downloadText")}
          confirmLabel={t("settings.download")}
          onConfirm={() => startDownload(confirming)}
          onCancel={() => setConfirming(null)}
        />
      )}
    </Section>
  );
}

type RowProps = {
  model: LocalModel;
  selected: boolean;
  onSelect: () => void;
  onDownload: () => void;
  onCancel: () => void;
};

function ModelRow({ model, selected, onSelect, onDownload, onCancel }: RowProps) {
  const t = useT();
  const parts = [t(`model.tier.${model.tier}`), formatSize(model.size, t)];
  parts.push(
    model.min_vram_gb > 0
      ? t("settings.needsGpu", { gb: model.min_vram_gb })
      : t("settings.forCpu"),
  );
  if (model.thinking) parts.push(t("model.canThink"));
  if (model.installed && model.source && model.source !== "tamra") {
    parts.push(t(SOURCE_KEYS[model.source]));
  }
  return (
    <div className={selected ? "model-row selected" : "model-row"}>
      <button
        type="button"
        role="radio"
        aria-checked={selected}
        className="model-pick"
        disabled={!model.installed}
        onClick={onSelect}
      >
        <RadioMark on={selected} />
        <span className="model-text">
          <span className="model-name">
            {model.name}
            {model.recommended && <span className="badge">{t("settings.recommended")}</span>}
          </span>
          <span className="model-detail">{parts.join(" · ")}</span>
          {model.state === "error" && model.error && (
            <span className="model-error">{t("settings.downloadFailed", { error: model.error })}</span>
          )}
        </span>
      </button>
      <ModelAction model={model} onDownload={onDownload} onCancel={onCancel} />
    </div>
  );
}

/** What a catalog model offers: Installed, Download, or a progress bar with Cancel. */
export function ModelAction({
  model,
  onDownload,
  onCancel,
}: {
  model: ModelStatus;
  onDownload: () => void;
  onCancel: () => void;
}) {
  const t = useT();
  const progress = percent(model);
  return (
    <div className="model-action">
      {model.installed ? (
        <span className="installed">
          <Check size={14} />
          {t("settings.installed")}
        </span>
      ) : isDownloading(model) ? (
        <div className="download">
          <div className="download-text">
            <span>
              {model.state === "verifying"
                ? t("settings.verifying")
                : t("settings.downloadingPercent", { percent: progress })}
            </span>
            {model.state === "downloading" && (
              <button
                type="button"
                className="link-button"
                aria-label={`${t("settings.cancelDownload")}: ${model.name}`}
                onClick={onCancel}
              >
                {t("common.cancel")}
              </button>
            )}
          </div>
          <div
            className="progress"
            role="progressbar"
            aria-label={model.name}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={model.state === "verifying" ? 100 : progress}
          >
            <div
              className="progress-fill"
              style={{ width: `${model.state === "verifying" ? 100 : progress}%` }}
            />
          </div>
        </div>
      ) : (
        <button
          type="button"
          className="btn"
          aria-label={`${t("settings.download")} ${model.name}`}
          onClick={onDownload}
        >
          {t("settings.download")}
        </button>
      )}
    </div>
  );
}

function ImportedRow({
  model,
  selected,
  onSelect,
  onUnlink,
}: {
  model: UncataloguedModel;
  selected: boolean;
  onSelect: () => void;
  onUnlink: () => void;
}) {
  const t = useT();
  return (
    <div className={selected ? "model-row selected" : "model-row"}>
      <button
        type="button"
        role="radio"
        aria-checked={selected}
        className="model-pick"
        onClick={onSelect}
      >
        <RadioMark on={selected} />
        <span className="model-text">
          <span className="model-name" title={model.path}>
            {model.name}
          </span>
          <span className="model-detail">
            {t(SOURCE_KEYS[model.source])} · {formatSize(model.size, t)}
          </span>
          <span className="model-warning">
            <AlertTriangle size={13} />
            {t("settings.uncatalogued")}
          </span>
        </span>
      </button>
      <div className="model-action">
        <span className="installed">
          <Check size={14} />
          {t("settings.installed")}
        </span>
        {model.source === "linked" && (
          <button
            type="button"
            className="link-button"
            aria-label={`${t("settings.unlink")}: ${model.name}`}
            title={t("settings.unlinkHint")}
            onClick={onUnlink}
          >
            {t("settings.unlink")}
          </button>
        )}
      </div>
    </div>
  );
}
