import { DEFAULT_SETTINGS } from "./settings";
import type { LocalModel, ModelsInfo, Settings } from "./types";

/** Fixtures for the tests that read GET /api/models and /api/settings. */

export function localModel(over: Partial<LocalModel>): LocalModel {
  return {
    id: "qwen3-4b",
    name: "Qwen3-4B",
    size: 2_500_000_000,
    installed: true,
    state: "idle",
    done: 0,
    total: 0,
    error: null,
    tier: "small",
    recommended: true,
    license: "apache-2.0",
    languages: ["th", "en"],
    context_length: 32768,
    thinking: true,
    min_vram_gb: 0,
    ...over,
  };
}

/** Qwen3-4B installed and active, Qwen3-8B downloading at 62%, and one imported file. */
export function modelsInfo(over: Partial<ModelsInfo> = {}): ModelsInfo {
  return {
    hardware: { ram_gb: 15.9, gpus: [{ name: "NVIDIA RTX 3060", vram_mb: 12282, integrated: false }] },
    recommended_tier: "small",
    active: { mode: "local", label: "Qwen3-4B", id: "qwen3-4b" },
    gpu_offload: null,
    local: [
      localModel({}),
      localModel({
        id: "qwen3-8b",
        name: "Qwen3-8B",
        tier: "medium",
        recommended: false,
        installed: false,
        state: "downloading",
        done: 62,
        total: 100,
      }),
      localModel({ id: "qwen3-14b", name: "Qwen3-14B", tier: "large", installed: false }),
    ],
    uncatalogued: [{ id: "import:mine.gguf", name: "mine", file: "mine.gguf", size: 8 }],
    ...over,
  };
}

export const saved = (over: Partial<Settings> = {}): Settings => ({ ...DEFAULT_SETTINGS, ...over });
