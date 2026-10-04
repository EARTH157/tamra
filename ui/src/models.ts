import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { LocalModel, ModelsInfo, Settings, UncataloguedModel } from "./types";

export type ModelsApi = {
  /** The last answer of GET /api/models; null until the first one arrives (or when it failed). */
  models: ModelsInfo | null;
  /** Ask again, e.g. when a menu opens or a download is running. */
  refresh: () => Promise<void>;
};

/**
 * The models the core knows about. They load on mount and again whenever the model choice in the
 * settings changes, because `active` follows it.
 */
export function useModels(settings: Pick<Settings, "mode" | "local_model_id">): ModelsApi {
  const [models, setModels] = useState<ModelsInfo | null>(null);
  const latest = useRef(0);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const refresh = useCallback(async () => {
    const call = ++latest.current;
    try {
      const loaded = await api<ModelsInfo>("GET", "/api/models");
      if (alive.current && call === latest.current) setModels(loaded);
    } catch {
      // The chip falls back to the settings; the next refresh tries again.
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh, settings.mode, settings.local_model_id]);

  return { models, refresh };
}

/** Local models to offer: installed ones, and ones being downloaded (shown, but not choosable). */
export function offeredLocal(models: ModelsInfo): LocalModel[] {
  return models.local.filter(
    (m) => m.installed || m.state === "downloading" || m.state === "verifying",
  );
}

export function isDownloading(model: LocalModel): boolean {
  return model.state === "downloading" || model.state === "verifying";
}

/** Download progress in whole percent (0 while the size is not known yet). */
export function percent(model: LocalModel): number {
  return model.total > 0 ? Math.min(100, Math.floor((model.done / model.total) * 100)) : 0;
}

/** The local model local mode would use: a catalog entry, an import, or only a label. */
export function activeLocal(
  models: ModelsInfo,
): { entry: LocalModel | null; imported: UncataloguedModel | null } {
  const id = models.active.id;
  return {
    entry: models.local.find((m) => m.id === id) ?? null,
    imported: models.uncatalogued.find((m) => m.id === id) ?? null,
  };
}

/** The name on the model chip: the cloud model in API mode, else the local model in use. */
export function activeName(models: ModelsInfo | null, settings: Settings): string | null {
  if (settings.mode === "api") return settings.api_model;
  if (!models) return null;
  const { entry, imported } = activeLocal(models);
  return entry?.name ?? imported?.name ?? models.active.label;
}

/**
 * Whether "Think longer" can be used. A local model whose catalog entry says `thinking: false`
 * cannot; an import has no entry to say so, and the cloud models can think.
 */
export function canThink(models: ModelsInfo | null, settings: Settings): boolean {
  if (settings.mode === "api" || !models) return true;
  const { entry } = activeLocal(models);
  return entry ? entry.thinking : true;
}
